"""API moteurs : état, télémétrie, santé, prédictions, FFT, XAI, rapports."""
from __future__ import annotations

import csv
import io
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlmodel import Session, desc, func, select

from ..db import get_session
from ..models import (Alert, HealthSnapshot, InferenceRecord, Intervention, Motor, TelemetryRecord,
                      WorkOrder, now_ts_ms)
from ..realtime import hub
from ..schemas import InterventionCreate
from ..services.common import logger, sign_payload
from ..services.ml_service import ml_service
from ..services.reporting import build_pdf

router = APIRouter(prefix="/api/motors", tags=["motors"])

VALID_MOTORS = ("M1", "M2")


def _check(motor_id: str) -> str:
    if motor_id not in VALID_MOTORS:
        raise HTTPException(404, f"Moteur inconnu : {motor_id} (attendu M1 ou M2)")
    return motor_id


# --------------------------------------------------------------------------
#  Liste et fiche moteur
# --------------------------------------------------------------------------
@router.get("", summary="Liste des moteurs avec leur état courant")
def list_motors(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    motors = session.exec(select(Motor)).all()
    out: List[Dict[str, Any]] = []
    for motor in motors:
        telemetry = hub.telemetry[motor.id].last() or {}
        inference = hub.inference[motor.id].last() or {}
        edge = hub.edge_inference[motor.id].last() or {}
        out.append({
            "id": motor.id, "name": motor.name, "label": motor.label, "side": motor.side,
            "model": motor.model, "nominal_rpm": motor.nominal_rpm,
            "nominal_current_a": motor.nominal_current_a, "nominal_temp_c": motor.nominal_temp_c,
            "running_hours": round(motor.running_hours + (telemetry.get("_uptime_h", 0) or 0), 2),
            "last_maintenance": motor.last_maintenance, "maintenance_count": motor.maintenance_count,
            "state": telemetry.get("state", "UNKNOWN"),
            "health_index": inference.get("health_index", 100.0),
            "anomaly_score": inference.get("anomaly_score", 0.0),
            "fault_class": inference.get("fault_class", "HEALTHY"),
            "rul_hours": inference.get("rul_hours", 0.0),
            "rpm": telemetry.get("speed", {}).get("rpm", 0.0),
            "power_w": telemetry.get("current", {}).get("power_w", 0.0),
            "temp_bearing_c": telemetry.get("temperature", {}).get("bearing_c", 0.0),
            "vibration_rms": telemetry.get("vibration", {}).get("rms", 0.0),
            "edge_health_index": edge.get("health_index"),
            "model_version": inference.get("model_version"),
            "baseline": motor.baseline,
        })
    # Garantit la présence de M1/M2 même si la base vient d'être créée
    known = {m["id"] for m in out}
    for mid in VALID_MOTORS:
        if mid not in known:
            out.append({"id": mid, "label": "Moteur GAUCHE" if mid == "M1" else "Moteur DROIT",
                        "side": "LEFT" if mid == "M1" else "RIGHT", "state": "UNKNOWN",
                        "health_index": 100.0, "fault_class": "HEALTHY", "rul_hours": 0.0})
    return sorted(out, key=lambda m: m["id"])


@router.get("/{motor_id}", summary="Fiche détaillée d'un moteur")
def get_motor(motor_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _check(motor_id)
    motor = session.get(Motor, motor_id)
    telemetry = hub.telemetry[motor_id].last() or {}
    inference = hub.inference[motor_id].last() or {}
    alerts = session.exec(
        select(Alert).where(Alert.motor_id == motor_id).order_by(desc(Alert.ts)).limit(20)
    ).all()
    return {
        "motor": motor.model_dump() if motor else {"id": motor_id},
        "telemetry": telemetry,
        "inference": inference,
        "alerts": [a.model_dump() for a in alerts],
        "edge_inference": hub.edge_inference[motor_id].last(),
        "uptime_s": telemetry.get("_uptime_s"),
        "running_hours": motor.running_hours if motor else 0.0,
    }


# --------------------------------------------------------------------------
#  Séries historiques
# --------------------------------------------------------------------------
@router.get("/{motor_id}/telemetry", summary="Télémétrie historique (from/to en ms)")
def telemetry_history(motor_id: str, from_ts: Optional[int] = Query(None, alias="from"),
                      to_ts: Optional[int] = Query(None, alias="to"),
                      limit: int = Query(2000, le=20000), source: str = Query("auto"),
                      session: Session = Depends(get_session)) -> Dict[str, Any]:
    _check(motor_id)
    now = now_ts_ms()
    to_ts = to_ts or now
    from_ts = from_ts or (to_ts - 3600_000)

    # Fenêtre courte (< 15 min) et mode "live" : on sert le buffer mémoire haute fréquence
    if source in ("auto", "buffer") and (to_ts - from_ts) <= 900_000:
        buffered = hub.telemetry[motor_id].snapshot(since_ts=from_ts)
        if len(buffered) >= 30:
            return {"motor_id": motor_id, "source": "buffer", "count": len(buffered),
                    "points": buffered[-limit:]}

    rows = session.exec(
        select(TelemetryRecord).where(
            TelemetryRecord.motor_id == motor_id,
            TelemetryRecord.ts >= from_ts, TelemetryRecord.ts <= to_ts,
        ).order_by(TelemetryRecord.ts).limit(limit)
    ).all()
    return {
        "motor_id": motor_id, "source": "database", "count": len(rows),
        "points": [{
            "ts": r.ts, "vibration": {"ax": r.vib_ax, "ay": r.vib_ay, "az": r.vib_az, "rms": r.vib_rms,
                                      "peak": r.vib_peak, "kurtosis": r.kurtosis,
                                      "crest_factor": r.crest_factor},
            "current": {"rms_a": r.current_rms, "peak_a": r.current_peak, "power_w": r.power_w},
            "temperature": {"motor_c": r.temp_motor_c, "bearing_c": r.temp_bearing_c,
                            "ambient_c": r.temp_ambient_c},
            "acoustic": {"spl_db": r.spl_db, "band_energy": r.band_energy},
            "speed": {"rpm": r.rpm, "steps_per_s": r.steps_per_s, "belt_mm_s": r.belt_mm_s},
            "state": r.state, "fft_peaks": r.fft_peaks,
        } for r in rows],
    }


@router.get("/{motor_id}/features", summary="Features historiques (1 Hz)")
def features_history(motor_id: str, from_ts: Optional[int] = Query(None, alias="from"),
                     limit: int = Query(600, le=5000), session: Session = Depends(get_session)) -> Dict[str, Any]:
    _check(motor_id)
    buffered = hub.features[motor_id].snapshot(limit=limit, since_ts=from_ts)
    if buffered:
        return {"motor_id": motor_id, "count": len(buffered), "points": buffered}
    from ..models import FeatureRecord
    rows = session.exec(
        select(FeatureRecord).where(FeatureRecord.motor_id == motor_id)
        .order_by(desc(FeatureRecord.ts)).limit(limit)
    ).all()
    return {"motor_id": motor_id, "count": len(rows),
            "points": [{"ts": r.ts, "features": r.features, "state": r.state} for r in reversed(rows)]}


@router.get("/{motor_id}/health", summary="Historique de l'indice de santé")
def health_history(motor_id: str, hours: int = Query(24, ge=1, le=720),
                   session: Session = Depends(get_session)) -> Dict[str, Any]:
    _check(motor_id)
    since = now_ts_ms() - hours * 3600_000
    rows = session.exec(
        select(HealthSnapshot).where(HealthSnapshot.motor_id == motor_id, HealthSnapshot.ts >= since)
        .order_by(HealthSnapshot.ts)
    ).all()
    buffered = hub.inference[motor_id].snapshot()
    points = [{"ts": r.ts, "health_index": r.health_index, "anomaly_score": r.anomaly_score,
               "fault_class": r.fault_class, "rpm": r.rpm, "vibration_rms": r.vibration_rms,
               "current_rms": r.current_rms, "temp_bearing_c": r.temp_bearing_c} for r in rows]
    if not points:
        points = [{"ts": i["ts"], "health_index": i["health_index"], "anomaly_score": i["anomaly_score"],
                   "fault_class": i["fault_class"]} for i in buffered]
    return {
        "motor_id": motor_id, "hours": hours, "count": len(points), "points": points,
        "current": hub.inference[motor_id].last(),
        "delta_24h": _delta_24h(points),
        "degradation_per_day": _degradation(points),
    }


def _delta_24h(points: List[Dict[str, Any]]) -> Optional[float]:
    if len(points) < 2:
        return None
    return round(points[-1]["health_index"] - points[0]["health_index"], 2)


def _degradation(points: List[Dict[str, Any]]) -> float:
    if len(points) < 5:
        return 0.0
    import numpy as np
    ts = np.array([p["ts"] for p in points], dtype=float) / 3.6e6
    hs = np.array([p["health_index"] for p in points], dtype=float)
    if ts[-1] - ts[0] < 1e-6:
        return 0.0
    return round(float(np.polyfit(ts, hs, 1)[0] * 24), 3)


# --------------------------------------------------------------------------
#  IA : prédictions, dégradation, XAI, FFT
# --------------------------------------------------------------------------
@router.get("/{motor_id}/prediction", summary="Dernière prédiction IA complète")
def prediction(motor_id: str) -> Dict[str, Any]:
    _check(motor_id)
    inference = hub.inference[motor_id].last()
    engine = ml_service.engine
    return {
        "motor_id": motor_id,
        "inference": inference,
        "history": hub.inference[motor_id].snapshot(limit=120),
        "degradation": ml_service.degradation_curve(motor_id),
        "radar": ml_service.radar(motor_id),
        "model": engine.model_info if engine else {},
        "edge_last": hub.edge_inference[motor_id].last(),
        "edge_comparison": hub.edge_comparison.get(motor_id),
    }


@router.get("/{motor_id}/degradation", summary="Courbe de dégradation + projection RUL")
def degradation(motor_id: str, hours: float = Query(168, ge=1, le=2160)) -> Dict[str, Any]:
    _check(motor_id)
    data = ml_service.degradation_curve(motor_id, hours=hours)
    inference = hub.inference[motor_id].last() or {}
    return {**data, "motor_id": motor_id, "rul_hours": inference.get("rul_hours"),
            "rul_confidence": inference.get("rul_confidence"), "critical_threshold": 30.0}


@router.get("/{motor_id}/radar", summary="Signature vibratoire actuelle vs baseline saine")
def radar(motor_id: str) -> Dict[str, Any]:
    _check(motor_id)
    return {"motor_id": motor_id, **ml_service.radar(motor_id)}


@router.get("/{motor_id}/fft", summary="Spectre FFT + fréquences de défaut caractéristiques")
def fft(motor_id: str) -> Dict[str, Any]:
    _check(motor_id)
    from ml.features import bearing_frequencies

    telemetry = hub.telemetry[motor_id].last() or {}
    features = hub.features[motor_id].last() or {}
    rpm = float(telemetry.get("speed", {}).get("rpm", 120.0)) or 120.0
    peaks = telemetry.get("fft_peaks") or []
    # Reconstruction d'un spectre utilisable pour le graphe (pics + plancher de bruit)
    spectrum = [{"freq": round(float(p["freq"]), 2), "amp": round(float(p["amp"]), 5)} for p in peaks]
    return {
        "motor_id": motor_id,
        "rpm": rpm,
        "spectrum": spectrum,
        "band_energy": telemetry.get("acoustic", {}).get("band_energy", []),
        "bearing_frequencies": bearing_frequencies(rpm),
        "harmonics": [round(rpm / 60.0 * k, 2) for k in (1, 2, 3, 4)],
        "features": features.get("features", {}),
        "timestamp": telemetry.get("ts"),
    }


@router.get("/{motor_id}/spectrogram", summary="Spectrogramme temps × fréquence (waterfall)")
def spectrogram(motor_id: str) -> Dict[str, Any]:
    _check(motor_id)
    data = ml_service.spectrogram(motor_id)
    return {"motor_id": motor_id, "columns": data, "count": len(data)}


@router.get("/{motor_id}/anomalies", summary="Historique des anomalies et diagnostics")
def anomalies(motor_id: str, hours: int = Query(168, ge=1, le=2160), fault_class: Optional[str] = None,
              limit: int = Query(200, le=2000), session: Session = Depends(get_session)) -> Dict[str, Any]:
    _check(motor_id)
    since = now_ts_ms() - hours * 3600_000
    query = select(InferenceRecord).where(
        InferenceRecord.motor_id == motor_id, InferenceRecord.ts >= since,
        InferenceRecord.fault_class != "HEALTHY",
    )
    if fault_class:
        query = query.where(InferenceRecord.fault_class == fault_class)
    rows = session.exec(query.order_by(desc(InferenceRecord.ts)).limit(limit)).all()
    return {"motor_id": motor_id, "count": len(rows),
            "items": [{"ts": r.ts, "fault_class": r.fault_class, "health_index": r.health_index,
                       "anomaly_score": r.anomaly_score, "rul_hours": r.rul_hours,
                       "explanation": r.explanation, "model_version": r.model_version} for r in rows]}


@router.get("/{motor_id}/xai", summary="Explication du dernier diagnostic (XAI)")
def xai(motor_id: str) -> Dict[str, Any]:
    _check(motor_id)
    inference = hub.inference[motor_id].last() or {}
    explanation = inference.get("explanation", [])
    if explanation:
        top = explanation[0]
        others = ", ".join(e.get("label", e["feature"]) for e in explanation[1:3])
        narrative = (
            f"La hausse de {top.get('label', top['feature'])} "
            f"(contribution {float(top.get('contribution', 0)) * 100:.0f} %) "
            + (f"combinée à {others} " if others else "")
            + f"oriente le diagnostic vers un défaut de type "
            f"{str(inference.get('fault_class', 'HEALTHY')).replace('_', ' ').lower()} "
            f"sur {motor_id}, avec un indice de santé de {inference.get('health_index')}/100."
        )
    else:
        narrative = f"Aucun écart notable par rapport à la signature de référence saine sur {motor_id}."
    return {
        "motor_id": motor_id,
        "inference": inference,
        "contributions": explanation,
        "narrative": narrative,
        "baseline_comparison": ml_service.radar(motor_id),
        "fault_probabilities": inference.get("fault_probabilities", {}),
        "model_version": inference.get("model_version"),
    }


# --------------------------------------------------------------------------
#  Interventions & rapport
# --------------------------------------------------------------------------
@router.get("/{motor_id}/interventions", summary="Journal des interventions du moteur")
def interventions(motor_id: str, session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    _check(motor_id)
    rows = session.exec(
        select(Intervention).where(Intervention.motor_id == motor_id).order_by(desc(Intervention.ts))
    ).all()
    return [r.model_dump() for r in rows]


@router.post("/{motor_id}/interventions", status_code=201, summary="Enregistrer une intervention")
def add_intervention(motor_id: str, payload: InterventionCreate,
                     session: Session = Depends(get_session)) -> Dict[str, Any]:
    _check(motor_id)
    if payload.motor_id and payload.motor_id != motor_id:
        raise HTTPException(400, f"Moteur incohérent : chemin={motor_id}, corps={payload.motor_id}")
    ts = now_ts_ms()
    intervention = Intervention(
        ts=ts, motor_id=motor_id, work_order_id=payload.work_order_id, technician=payload.technician,
        action=payload.action, description=payload.description, duration_min=payload.duration_min,
        parts_used=payload.parts_used, cost_eur=payload.cost_eur,
    )
    intervention.signature = sign_payload({"ts": ts, "motor_id": motor_id, "action": payload.action,
                                           "technician": payload.technician})
    session.add(intervention)
    motor = session.get(Motor, motor_id)
    if motor:
        motor.maintenance_count += 1
        from datetime import datetime, timezone
        motor.last_maintenance = datetime.now(timezone.utc).replace(tzinfo=None)
        motor.running_hours = 0.0
        session.add(motor)
    session.commit()
    session.refresh(intervention)
    logger.info("Intervention enregistrée sur %s : %s", motor_id, payload.action)
    return intervention.model_dump()


@router.get("/{motor_id}/work-orders", summary="Ordres de travail du moteur")
def work_orders(motor_id: str, session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    _check(motor_id)
    rows = session.exec(select(WorkOrder).where(WorkOrder.motor_id == motor_id)
                        .order_by(desc(WorkOrder.created_at))).all()
    return [r.model_dump() for r in rows]


@router.get("/{motor_id}/report", summary="Rapport PDF du moteur (téléchargeable)")
def motor_report(motor_id: str, hours: int = Query(24, ge=1, le=720)) -> Response:
    _check(motor_id)
    pdf = build_pdf(motor_id, hours=hours)
    filename = f"rapport-{motor_id}-{time.strftime('%Y%m%d-%H%M')}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/{motor_id}/export.csv", summary="Export CSV de la télémétrie")
def export_csv(motor_id: str, hours: int = Query(24, ge=1, le=720),
               session: Session = Depends(get_session)) -> StreamingResponse:
    _check(motor_id)
    since = now_ts_ms() - hours * 3600_000
    rows = session.exec(
        select(TelemetryRecord).where(TelemetryRecord.motor_id == motor_id, TelemetryRecord.ts >= since)
        .order_by(TelemetryRecord.ts).limit(50000)
    ).all()

    def _generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["ts", "iso_time", "motor_id", "vib_rms", "vib_peak", "kurtosis", "crest_factor",
                         "current_rms", "current_peak", "power_w", "temp_motor_c", "temp_bearing_c",
                         "temp_ambient_c", "spl_db", "rpm", "belt_mm_s", "state"])
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        for r in rows:
            from datetime import datetime, timezone
            writer.writerow([r.ts, datetime.fromtimestamp(r.ts / 1000, tz=timezone.utc).isoformat(),
                             r.motor_id, r.vib_rms, r.vib_peak, r.kurtosis, r.crest_factor,
                             r.current_rms, r.current_peak, r.power_w, r.temp_motor_c, r.temp_bearing_c,
                             r.temp_ambient_c, r.spl_db, r.rpm, r.belt_mm_s, r.state])
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    return StreamingResponse(_generate(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{motor_id}-telemetry.csv"'})
