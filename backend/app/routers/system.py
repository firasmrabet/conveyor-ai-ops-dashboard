"""API système : santé, vue d'ensemble, paramètres, rapports PDF, WebSocket temps réel."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlmodel import Session, func, select

from ..config import settings
from ..db import get_session
from ..models import (Alert, AppSetting, Device, HealthSnapshot, Intervention, ModelVersion, Motor,
                      TelemetryRecord, User, WorkOrder, now_ts_ms)
from ..realtime import hub, redis_client
from ..schemas import SettingsUpdate
from ..services.alerting import alert_engine
from ..services.common import logger, uptime_human
from ..services.ml_service import ml_service
from ..services.mqtt_client import mqtt_bridge
from ..services.reliability import availability_pct
from ..services.reporting import build_pdf
from ..services.scheduler import scheduler_service
from ..services.simulator import simulator

router = APIRouter(tags=["system"])

STARTED_AT = time.time()

DEFAULT_SETTINGS: Dict[str, Any] = {
    "units": {"temperature": "C", "vibration": "g", "speed": "rpm", "power": "W"},
    "language": "fr",
    "theme": "dark",
    "notifications": {"inapp": True, "sound": True, "email": False, "webhook": False},
    "thresholds": {"health_warning": 55, "health_critical": 30, "vibration_warning": 0.35,
                   "vibration_critical": 0.55, "temp_warning": 65, "temp_critical": 75},
    "reduced_motion": False,
    "retention_days": {"raw": settings.raw_retention_days, "features": settings.feature_retention_days},
}


# ==========================================================================
#  Santé / vue d'ensemble
# ==========================================================================
@router.get("/api/health", summary="Sonde de santé du backend (utilisée par Docker/K8s)")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "mode": settings.mode,
        "uptime_s": round(time.time() - STARTED_AT, 1),
        "uptime": uptime_human(time.time() - STARTED_AT),
        "clients_ws": len(hub.clients),
        "scheduler": scheduler_service.backend,
        "simulator_running": simulator.is_running,
        "mqtt": mqtt_bridge.status,
        "model": (ml_service.engine.model_info if ml_service.engine else {"type": "none"}),
        "time": now_ts_ms(),
    }


@router.get("/api/overview", summary="Vue d'ensemble temps réel du dashboard")
def overview(session: Session = Depends(get_session)) -> Dict[str, Any]:
    motors: Dict[str, Any] = {}
    for motor_id in ("M1", "M2"):
        telemetry = hub.telemetry[motor_id].last() or {}
        inference = hub.inference[motor_id].last() or {}
        motors[motor_id] = {
            "telemetry": telemetry,
            "inference": inference,
            "edge": hub.edge_inference[motor_id].last() or {},
            "sparkline": [
                {"ts": p.get("ts"), "vibration": p.get("vibration", {}).get("rms"),
                 "current": p.get("current", {}).get("rms_a"),
                 "temperature": p.get("temperature", {}).get("bearing_c"),
                 "rpm": p.get("speed", {}).get("rpm")}
                for p in hub.telemetry[motor_id].snapshot(limit=60)
            ],
        }

    alerts = session.exec(select(Alert).order_by(Alert.ts.desc()).limit(30)).all()
    defects_24h = session.exec(
        select(func.count(Alert.id)).where(Alert.ts >= now_ts_ms() - 86_400_000,
                                           Alert.type.in_(["ANOMALY", "AI_CLASS"]))
    ).one()
    alerts_open = session.exec(select(func.count(Alert.id)).where(Alert.status == "NEW")).one()
    interventions = session.exec(select(Intervention)).all()
    work_orders = session.exec(select(WorkOrder).where(WorkOrder.status != "DONE")).all()

    health_m1 = motors["M1"]["inference"].get("health_index", 100.0)
    health_m2 = motors["M2"]["inference"].get("health_index", 100.0)
    running = hub.status.get("running", False)

    total_power = sum(motors[m]["telemetry"].get("current", {}).get("power_w", 0.0) for m in ("M1", "M2"))
    return {
        "generated_at": now_ts_ms(),
        "kpis": {
            "line_state": "RUNNING" if running else ("STOPPED" if not running else "UNKNOWN"),
            "availability_pct": _availability(session),
            "global_health": round((health_m1 + health_m2) / 2, 1),
            "health_m1": health_m1,
            "health_m2": health_m2,
            "active_alerts": alerts_open or 0,
            "defects_24h": defects_24h or 0,
            "total_power_w": round(total_power, 2),
            "parcels_per_min": hub.status.get("parcels_per_min", 0.0),
            "belt_mm_s": hub.status.get("belt_mm_s", 0.0),
            "running_hours_total": round(sum(
                (session.get(Motor, m).running_hours if session.get(Motor, m) else 0.0)
                for m in ("M1", "M2")), 1),
            "open_work_orders": len(work_orders),
            "interventions_total": len(interventions),
        },
        "motors": motors,
        "status": hub.status,
        "alerts": [a.model_dump() for a in alerts],
        "prescriptions": alert_engine.prescriptions(),
        "rates": hub.rates,
        "mqtt": mqtt_bridge.stats(),
        "model": (ml_service.engine.model_info if ml_service.engine else {"type": "none"}),
        "edge_comparison": hub.edge_comparison,
        "simulator": simulator.active_scenario,
    }


#: Fenêtre du KPI de disponibilité : alignée sur l'indicateur affiché (30 derniers jours).
AVAILABILITY_WINDOW_H = 24 * 30


def _availability(session: Session) -> float:
    """Disponibilité (%) de la ligne sur 30 jours — formule partagée avec /api/analytics.

    Utilise `services.reliability` (temps pondéré par état) afin que le bandeau de KPI
    de la salle de contrôle et la page analytique affichent EXACTEMENT la même valeur.
    """
    value = availability_pct(session, ("M1", "M2"), window_h=AVAILABILITY_WINDOW_H)
    if value is None:
        # Base vide : on retombe sur l'état du simulateur/broker.
        return round(100.0 if hub.status.get("running") else 0.0, 2)
    return value


# ==========================================================================
#  Paramètres
# ==========================================================================
@router.get("/api/settings", summary="Paramètres de l'application")
def get_settings_api(session: Session = Depends(get_session)) -> Dict[str, Any]:
    stored = session.exec(select(AppSetting)).all()
    values = {**DEFAULT_SETTINGS, **{s.key: s.value for s in stored}}
    return {
        "values": values,
        "environment": {
            "mode": settings.mode,
            "mqtt_enabled": settings.mqtt_enabled,
            "redis_enabled": settings.redis_enabled,
            "ml_active_bundle": settings.ml_active_bundle,
            "ml_model_version": settings.ml_model_version,
            "raw_retention_days": settings.raw_retention_days,
            "feature_retention_days": settings.feature_retention_days,
            "cors_origins": settings.cors_origins,
            # Durée de vie des jetons JWT : affichée dans la page Paramètres
            # (sessions), donc exposée par l'API plutôt que codée en dur côté UI.
            "session_expire_minutes": settings.access_token_expire_minutes,
        },
    }


@router.put("/api/settings", summary="Mettre à jour les paramètres")
def update_settings(payload: SettingsUpdate, session: Session = Depends(get_session)) -> Dict[str, Any]:
    for key, value in payload.values.items():
        row = session.get(AppSetting, key)
        if row:
            row.value = value if isinstance(value, dict) else {"value": value}
            row.updated_at = now_ts_ms()
        else:
            row = AppSetting(key=key, value=value if isinstance(value, dict) else {"value": value},
                             updated_at=now_ts_ms())
        session.add(row)
    session.commit()
    return get_settings_api(session)


@router.get("/api/audit", summary="Journal d'audit (actions sensibles)")
def audit_log(session: Session = Depends(get_session)) -> Dict[str, Any]:
    from ..models import SecurityEvent

    events = session.exec(select(SecurityEvent).order_by(SecurityEvent.ts.desc()).limit(100)).all()
    interventions = session.exec(select(Intervention).order_by(Intervention.ts.desc()).limit(30)).all()
    return {"security_events": [e.model_dump() for e in events],
            "interventions": [i.model_dump() for i in interventions]}


# ==========================================================================
#  Rapports / exports globaux
# ==========================================================================
@router.post("/api/reports/generate", summary="Générer un rapport PDF (moteur ou global)")
def generate_report(motor_id: Optional[str] = Query(None, pattern="^(M1|M2)$"),
                    hours: int = Query(24, ge=1, le=720)) -> StreamingResponse:
    pdf = build_pdf(motor_id, hours=hours)
    name = f"rapport-{motor_id or 'convoyeur'}-{time.strftime('%Y%m%d-%H%M')}.pdf"
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/export/full.csv", summary="Export CSV complet (télémétrie + IA)")
def export_full(hours: int = Query(6, ge=1, le=168), session: Session = Depends(get_session)) -> StreamingResponse:
    since = now_ts_ms() - hours * 3600_000

    def _rows():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        header = ["ts", "motor_id", "vib_rms", "kurtosis", "current_rms", "power_w", "temp_motor_c",
                  "temp_bearing_c", "rpm", "health_index", "anomaly_score", "fault_class"]
        writer.writerow(header)
        yield buffer.getvalue()
        buffer.seek(0); buffer.truncate(0)
        for motor_id in ("M1", "M2"):
            telemetry = {p["ts"]: p for p in hub.telemetry[motor_id].snapshot()}
            inference = {i["ts"]: i for i in hub.inference[motor_id].snapshot()}
            for ts in sorted(set(telemetry) | set(inference)):
                t = telemetry.get(ts, {})
                i = inference.get(ts, {})
                writer.writerow([
                    ts, motor_id, t.get("vibration", {}).get("rms"), t.get("vibration", {}).get("kurtosis"),
                    t.get("current", {}).get("rms_a"), t.get("current", {}).get("power_w"),
                    t.get("temperature", {}).get("motor_c"), t.get("temperature", {}).get("bearing_c"),
                    t.get("speed", {}).get("rpm"), i.get("health_index"), i.get("anomaly_score"),
                    i.get("fault_class"),
                ])
                yield buffer.getvalue()
                buffer.seek(0); buffer.truncate(0)

    return StreamingResponse(_rows(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="export-complet.csv"'})


@router.get("/api/system/info", summary="Informations système détaillées (soutenance)")
def system_info() -> Dict[str, Any]:
    return {
        "app": settings.app_name,
        "mode": settings.mode,
        "version": "1.0.0",
        "stack": {
            "backend": "FastAPI + SQLModel + paho-mqtt + scikit-learn",
            "frontend": "Next.js 14 + TypeScript + Tailwind + shadcn/ui + Framer Motion + "
                        "react-three-fiber + Recharts/ECharts",
            "transport": "WebSocket (temps réel) + MQTT (capteurs) + REST (historique)",
        },
        "pipeline": [
            "ESP32 → MQTT JSON (5–10 Hz)",
            "Ingestion + validation Pydantic",
            "Extraction de features (RMS, kurtosis, crest, FFT 1x/2x/HF)",
            "Détection d'anomalie (IsolationForest / signatures physiques)",
            "Classification du défaut (RandomForest 7 classes)",
            "RUL (régression) + bande de confiance",
            "Fusion Health Index (0–100) + XAI",
            "Alertes (règles + IA) et maintenance prescriptive",
        ],
        "buffers": {
            "telemetry": {m: len(hub.telemetry[m]) for m in ("M1", "M2")},
            "features": {m: len(hub.features[m]) for m in ("M1", "M2")},
            "inference": {m: len(hub.inference[m]) for m in ("M1", "M2")},
            "alerts": len(hub.alerts),
        },
        "clients": len(hub.clients),
        "redis": {"enabled": settings.redis_enabled, "available": redis_client.available},
        "uptime": uptime_human(time.time() - STARTED_AT),
    }


# ==========================================================================
#  WebSocket temps réel
# ==========================================================================
ws_router = APIRouter()


@ws_router.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket) -> None:
    """Flux temps réel : télémétrie, inférences, alertes, status, rates.

    Le client peut envoyer :
      {"type": "subscribe", "motors": ["M1"]}   → filtre par moteur
      {"type": "ping"}                          → réponse pong (latence)
    """
    await hub.connect(websocket)
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=25.0)
            except asyncio.TimeoutError:
                # Heartbeat serveur : détecte les connexions mortes sans bloquer
                await websocket.send_text(json.dumps({"type": "ping", "ts": int(time.time() * 1000)}))
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = message.get("type")
            if msg_type == "subscribe":
                hub.update_subscription(websocket, message.get("motors", ["M1", "M2"]))
                await websocket.send_text(json.dumps({"type": "subscribed",
                                                      "motors": hub.client_meta[websocket]["motors"]}))
            elif msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "ts": int(time.time() * 1000)}))
            elif msg_type == "snapshot":
                await websocket.send_text(json.dumps({"type": "snapshot", "data": hub.snapshot()}))
    except WebSocketDisconnect:
        hub.disconnect(websocket)
    except Exception as exc:  # pragma: no cover
        logger.debug("WebSocket fermé : %s", exc)
        hub.disconnect(websocket)
