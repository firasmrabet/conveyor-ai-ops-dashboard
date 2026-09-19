"""Analyse historique : heatmaps, comparateur M1/M2, corrélations, fiabilité, exports."""
from __future__ import annotations

import csv
import io
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, desc, select

from ..db import get_session
from ..models import (Alert, HealthSnapshot, HourlyAggregate, InferenceRecord, Intervention,
                      TelemetryRecord, WorkOrder, now_ts_ms)
from ..realtime import hub
from ..services.reliability import availability_pct, downtime_hours, stops_count
from ..services.reporting import build_pdf

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

RANGES = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}


def _window(range_key: str, from_ts: Optional[int], to_ts: Optional[int]) -> tuple[int, int]:
    to_ = to_ts or now_ts_ms()
    from_ = from_ts or (to_ - RANGES.get(range_key, 24) * 3600_000)
    return int(from_), int(to_)


# --------------------------------------------------------------------------
@router.get("/summary", summary="Synthèse de la fenêtre d'analyse")
def summary(range: str = Query("24h"), from_ts: Optional[int] = Query(None, alias="from"),
            to_ts: Optional[int] = Query(None, alias="to"), motors: str = Query("M1,M2"),
            session: Session = Depends(get_session)) -> Dict[str, Any]:
    start, end = _window(range, from_ts, to_ts)
    selected = [m.strip().upper() for m in motors.split(",") if m.strip().upper() in ("M1", "M2")]

    out: Dict[str, Any] = {"range": range, "from": start, "to": end, "motors": {}}
    for motor_id in selected:
        rows = session.exec(
            select(HealthSnapshot).where(HealthSnapshot.motor_id == motor_id,
                                         HealthSnapshot.ts >= start, HealthSnapshot.ts <= end)
            .order_by(HealthSnapshot.ts)
        ).all()
        alerts = session.exec(select(Alert).where(Alert.motor_id == motor_id, Alert.ts >= start)).all()
        out["motors"][motor_id] = {
            "samples": len(rows),
            "avg_health": round(float(np.mean([r.health_index for r in rows])), 1) if rows else None,
            "min_health": round(float(np.min([r.health_index for r in rows])), 1) if rows else None,
            "max_vibration": round(float(np.max([r.vibration_rms for r in rows])), 3) if rows else None,
            "avg_vibration": round(float(np.mean([r.vibration_rms for r in rows])), 3) if rows else None,
            "avg_current": round(float(np.mean([r.current_rms for r in rows])), 3) if rows else None,
            "avg_temp": round(float(np.mean([r.temp_bearing_c for r in rows])), 1) if rows else None,
            "max_temp": round(float(np.max([r.temp_bearing_c for r in rows])), 1) if rows else None,
            "avg_rpm": round(float(np.mean([r.rpm for r in rows])), 1) if rows else None,
            "anomalies": sum(1 for r in rows if r.anomaly_score > 0.55),
            "alerts": len(alerts),
            "critical_alerts": len([a for a in alerts if a.severity == "CRITICAL"]),
            "energy_kwh": _energy_kwh(rows),
        }
    return out


def _energy_kwh(rows: List[HealthSnapshot]) -> float:
    if not rows:
        return 0.0
    # Approximation : puissance moyenne reconstruite depuis le courant (12 V nominal)
    avg_current = float(np.mean([r.current_rms for r in rows]))
    hours = (rows[-1].ts - rows[0].ts) / 3.6e6
    return round(avg_current * 12.0 * hours / 1000, 4)


@router.get("/heatmap", summary="Heatmap calendaire de santé (jour de semaine × heure)")
def heatmap(range_key: str = Query("7d", alias="range"), motor_id: str = Query("M1"),
            session: Session = Depends(get_session)) -> Dict[str, Any]:
    start, end = _window(range_key, None, None)
    rows = session.exec(
        select(HealthSnapshot).where(HealthSnapshot.motor_id == motor_id,
                                     HealthSnapshot.ts >= start, HealthSnapshot.ts <= end)
    ).all()
    buckets: Dict[tuple[int, int], List[float]] = {}
    for r in rows:
        dt = datetime.fromtimestamp(r.ts / 1000, tz=timezone.utc)
        buckets.setdefault((dt.weekday(), dt.hour), []).append(r.health_index)
    # Agrégats horaires en secours (historique long)
    if not rows:
        hrows = session.exec(
            select(HourlyAggregate).where(HourlyAggregate.motor_id == motor_id,
                                          HourlyAggregate.bucket_ts >= start)
        ).all()
        for r in hrows:
            dt = datetime.fromtimestamp(r.bucket_ts / 1000, tz=timezone.utc)
            buckets.setdefault((dt.weekday(), dt.hour), []).append(r.avg_health)

    cells = [{"day": d, "hour": h, "health": round(float(np.mean(v)), 1), "samples": len(v)}
             for (d, h), v in sorted(buckets.items())]
    # Attention : ne pas nommer la variable `range`, elle masquerait la fonction native.
    matrix: List[List[Optional[float]]] = [[None] * 24 for _ in range(7)]
    for cell in cells:
        matrix[cell["day"]][cell["hour"]] = cell["health"]
    return {"motor_id": motor_id, "range": range_key, "cells": cells, "matrix": matrix,
            "days": ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]}


@router.get("/comparison", summary="Comparateur M1 vs M2 + écart de synchronisation")
def comparison(range: str = Query("24h"), metric: str = Query("health_index"),
               session: Session = Depends(get_session)) -> Dict[str, Any]:
    start, end = _window(range, None, None)
    allowed = ("health_index", "vibration_rms", "current_rms", "temp_bearing_c", "rpm", "anomaly_score")
    metric = metric if metric in allowed else "health_index"

    series: Dict[str, List[Dict[str, Any]]] = {}
    for motor_id in ("M1", "M2"):
        rows = session.exec(
            select(HealthSnapshot).where(HealthSnapshot.motor_id == motor_id,
                                         HealthSnapshot.ts >= start)
            .order_by(HealthSnapshot.ts)
        ).all()
        if not rows:
            rows = []
        series[motor_id] = [{"ts": r.ts, "value": getattr(r, metric)} for r in rows]

    # Écart de synchronisation : différence de vitesse moyenne/écart-type entre moteurs
    sync = _sync_analysis(series)
    return {"metric": metric, "range": range, "series": series, "sync": sync,
            "from": start, "to": end}


def _sync_analysis(series: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    m1 = [p["value"] for p in series.get("M1", [])]
    m2 = [p["value"] for p in series.get("M2", [])]
    n = min(len(m1), len(m2))
    if n < 5:
        return {"delta_mean": None, "delta_std": None, "max_gap": None, "aligned": False, "samples": n}
    a, b = np.array(m1[:n]), np.array(m2[:n])
    delta = a - b
    correlation = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 1e-9 and np.std(b) > 1e-9 else 0.0
    return {
        "samples": n,
        "delta_mean": round(float(np.mean(delta)), 3),
        "delta_std": round(float(np.std(delta)), 3),
        "max_gap": round(float(np.max(np.abs(delta))), 3),
        "correlation": round(correlation, 4),
        "aligned": abs(float(np.mean(delta))) < 3.0,
        "alerts": [
            {"ts": series["M1"][i]["ts"], "delta": round(float(delta[i]), 3)}
            for i in np.argsort(-np.abs(delta))[:10]
        ],
    }


@router.get("/correlation", summary="Corrélations température ↔ courant ↔ vibration")
def correlation(range: str = Query("24h"), motor_id: str = Query("M1"),
                session: Session = Depends(get_session)) -> Dict[str, Any]:
    start, end = _window(range, None, None)
    rows = session.exec(
        select(HealthSnapshot).where(HealthSnapshot.motor_id == motor_id,
                                     HealthSnapshot.ts >= start).limit(5000)
    ).all()
    if not rows:  # repli sur la télémétrie brute
        tels = session.exec(
            select(TelemetryRecord).where(TelemetryRecord.motor_id == motor_id,
                                          TelemetryRecord.ts >= start).limit(5000)
        ).all()
        points = [{"ts": t.ts, "vibration": t.vib_rms, "current": t.current_rms, "temperature": t.temp_bearing_c}
                  for t in tels]
    else:
        points = [{"ts": r.ts, "vibration": r.vibration_rms, "current": r.current_rms,
                   "temperature": r.temp_bearing_c} for r in rows]

    def _corr(x_key: str, y_key: str) -> Dict[str, Any]:
        if len(points) < 5:
            return {"r": None, "slope": None, "intercept": None, "r2": None}
        x = np.array([p[x_key] for p in points], dtype=float)
        y = np.array([p[y_key] for p in points], dtype=float)
        if np.std(x) < 1e-9 or np.std(y) < 1e-9:
            return {"r": 0.0, "slope": 0.0, "intercept": float(np.mean(y)), "r2": 0.0}
        slope, intercept = np.polyfit(x, y, 1)
        r = float(np.corrcoef(x, y)[0, 1])
        return {"r": round(r, 4), "slope": round(float(slope), 4),
                "intercept": round(float(intercept), 4), "r2": round(r ** 2, 4)}

    return {
        "motor_id": motor_id, "range": range, "points": points[-2000:], "count": len(points),
        "pairs": {
            "current_vs_temperature": _corr("current", "temperature"),
            "vibration_vs_current": _corr("vibration", "current"),
            "vibration_vs_temperature": _corr("vibration", "temperature"),
        },
    }


@router.get("/fault-distribution", summary="Distribution des défauts (donut + Pareto)")
def fault_distribution(range: str = Query("7d"), session: Session = Depends(get_session)) -> Dict[str, Any]:
    start, end = _window(range, None, None)
    rows = session.exec(
        select(InferenceRecord).where(InferenceRecord.ts >= start, InferenceRecord.fault_class != "HEALTHY")
    ).all()
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r.fault_class] = counts.get(r.fault_class, 0) + 1
    total = sum(counts.values()) or 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    cumulative = 0.0
    pareto = []
    for label, count in ordered:
        share = count / total * 100
        cumulative += share
        pareto.append({"fault_class": label, "count": count, "share_pct": round(share, 2),
                       "cumulative_pct": round(cumulative, 2)})
    alerts_count = session.exec(select(Alert).where(Alert.ts >= start)).all()
    return {
        "range": range, "total_anomalies": total if counts else 0,
        "distribution": [{"fault_class": k, "count": v, "share_pct": round(v / total * 100, 2)}
                         for k, v in ordered],
        "pareto": pareto,
        "by_severity": {
            sev: len([a for a in alerts_count if a.severity == sev]) for sev in ("INFO", "WARNING", "CRITICAL")
        },
        "by_origin": {
            origin: len([a for a in alerts_count if a.origin == origin])
            for origin in ("RULES", "ML", "EDGE", "DEVICE")
        },
    }


@router.get("/reliability", summary="MTBF, MTTR, arrêts, faux positifs")
def reliability(range: str = Query("30d"), session: Session = Depends(get_session)) -> Dict[str, Any]:
    start, end = _window(range, None, None)
    out: Dict[str, Any] = {"range": range, "motors": {}}
    for motor_id in ("M1", "M2"):
        snapshots = session.exec(
            select(HealthSnapshot).where(HealthSnapshot.motor_id == motor_id, HealthSnapshot.ts >= start)
            .order_by(HealthSnapshot.ts)
        ).all()
        alerts = session.exec(select(Alert).where(Alert.motor_id == motor_id, Alert.ts >= start)).all()
        interventions = session.exec(
            select(Intervention).where(Intervention.motor_id == motor_id, Intervention.ts >= start)
        ).all()
        work_orders = session.exec(select(WorkOrder).where(WorkOrder.motor_id == motor_id)).all()

        # Arrêts : transitions état exploitable → état non exploitable, via le module
        # partagé (même définition que la salle de contrôle et que /api/overview).
        stops = stops_count(session, motor_id, start)
        availability = availability_pct(session, [motor_id], since_ms=start)
        if availability is None:
            availability = 100.0 if (snapshots or not alerts) else 0.0
        failures = [a for a in alerts if a.severity == "CRITICAL"]
        resolved = [a for a in alerts if a.status == "RESOLVED"]
        # Faux positifs = alertes acquittées/classées sans intervention associée
        false_positives = max(0, len(resolved) - len(interventions))
        span_h = max((end - start) / 3.6e6, 1e-6)
        out["motors"][motor_id] = {
            "mtbf_h": round(span_h / max(len(failures), 1), 2),
            "mttr_h": round(sum(i.duration_min for i in interventions) / max(len(interventions), 1) / 60.0, 2),
            "failures": len(failures),
            "stops": stops,
            "interventions": len(interventions),
            "open_work_orders": len([w for w in work_orders if w.status != "DONE"]),
            "false_positive_rate": round(false_positives / max(len(alerts), 1), 4),
            "availability_pct": availability,
            "downtime_h": downtime_hours(availability, span_h),
            "maintenance_cost_eur": round(sum(i.cost_eur for i in interventions), 2),
            "samples": len(snapshots),
        }
    return out


@router.get("/hourly", summary="Série d'agrégats horaires (tendances longues)")
def hourly(days: int = Query(7, ge=1, le=90), motor_id: str = Query("M1"),
           session: Session = Depends(get_session)) -> Dict[str, Any]:
    start = now_ts_ms() - days * 86_400_000
    rows = session.exec(
        select(HourlyAggregate).where(HourlyAggregate.motor_id == motor_id,
                                      HourlyAggregate.bucket_ts >= start)
        .order_by(HourlyAggregate.bucket_ts)
    ).all()
    return {"motor_id": motor_id, "days": days,
            "points": [{"ts": r.bucket_ts, "avg_health": round(r.avg_health, 2),
                        "min_health": round(r.min_health, 2),
                        "avg_vibration": round(r.avg_vibration_rms, 4),
                        "avg_current": round(r.avg_current_rms, 4),
                        "avg_temp": round(r.avg_temp_bearing, 2),
                        "anomalies": r.anomaly_count} for r in rows]}


@router.get("/export.csv", summary="Export CSV consolidé (analytics)")
def export_analytics_csv(range: str = Query("24h"), motors: str = Query("M1,M2"),
                         session: Session = Depends(get_session)) -> StreamingResponse:
    start, end = _window(range, None, None)
    selected = [m.strip().upper() for m in motors.split(",") if m.strip().upper() in ("M1", "M2")]

    def _generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["ts", "iso_time", "motor_id", "health_index", "anomaly_score", "fault_class",
                         "vibration_rms", "current_rms", "temp_bearing_c", "rpm", "state"])
        yield buffer.getvalue()
        buffer.seek(0); buffer.truncate(0)
        for motor_id in selected:
            rows = session.exec(
                select(HealthSnapshot).where(HealthSnapshot.motor_id == motor_id,
                                             HealthSnapshot.ts >= start)
                .order_by(HealthSnapshot.ts).limit(50000)
            ).all()
            for r in rows:
                writer.writerow([r.ts, datetime.fromtimestamp(r.ts / 1000, tz=timezone.utc).isoformat(),
                                 motor_id, r.health_index, r.anomaly_score, r.fault_class, r.vibration_rms,
                                 r.current_rms, r.temp_bearing_c, r.rpm, r.state])
                yield buffer.getvalue()
                buffer.seek(0); buffer.truncate(0)

    return StreamingResponse(_generate(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="analytics-{range}.csv"'})


@router.get("/report", summary="Rapport PDF global (M1+M2)")
def global_report(hours: int = Query(24, ge=1, le=720)) -> StreamingResponse:
    pdf = build_pdf(None, hours=hours)
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf",
                             headers={"Content-Disposition":
                                      f'attachment; filename="rapport-convoyeur-{time.strftime("%Y%m%d-%H%M")}.pdf"'})
