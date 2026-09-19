"""Pipeline d'ingestion : MQTT/simulateur → validation → buffers → IA → base → WebSocket.

Chemin d'une donnée :
    payload brut
      → validation Pydantic (TelemetryPayload)
      → buffer circulaire en mémoire (10 s haute fréquence pour l'UI)
      → diffusion WebSocket (throttlée côté client)
      → extraction de features (1 Hz)
      → inférence IA (ml_run_inference_hz)
      → persistance (télémétrie sous-échantillonnée, features, inférences, snapshots)
      → moteur d'alertes (seuils + IA)
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from pydantic import ValidationError

from ..config import settings
from ..db import session_scope
from ..models import TelemetryRecord
from ..realtime import hub
from ..schemas import (ConveyorStatusPayload, EdgeInferencePayload, FeaturePayload,
                       HeartbeatPayload, TelemetryPayload)
from .alerting import alert_engine
from .common import logger, safe_float
from .ml_service import ml_service

# Sous-échantillonnage : on persiste la télémétrie brute à ~1 Hz pour la rétention
_persist_acc: Dict[str, float] = {"M1": 0.0, "M2": 0.0}
_last_persist_ts: Dict[str, float] = {"M1": 0.0, "M2": 0.0}
_last_feature_ts: Dict[str, float] = {"M1": 0.0, "M2": 0.0}


def _persist_telemetry(motor_id: str, tel: Dict[str, Any]) -> None:
    with session_scope() as session:
        session.add(TelemetryRecord(
            ts=int(tel["ts"]), motor_id=motor_id, device_id=str(tel.get("device_id", "esp32-conveyor-01")),
            vib_ax=safe_float(tel["vibration"].get("ax")), vib_ay=safe_float(tel["vibration"].get("ay")),
            vib_az=safe_float(tel["vibration"].get("az")), vib_rms=safe_float(tel["vibration"].get("rms")),
            vib_peak=safe_float(tel["vibration"].get("peak")), kurtosis=safe_float(tel["vibration"].get("kurtosis"), 3.0),
            crest_factor=safe_float(tel["vibration"].get("crest_factor")),
            current_rms=safe_float(tel["current"].get("rms_a")), current_peak=safe_float(tel["current"].get("peak_a")),
            power_w=safe_float(tel["current"].get("power_w")),
            temp_motor_c=safe_float(tel["temperature"].get("motor_c")),
            temp_bearing_c=safe_float(tel["temperature"].get("bearing_c")),
            temp_ambient_c=safe_float(tel["temperature"].get("ambient_c")),
            spl_db=safe_float(tel["acoustic"].get("spl_db")),
            rpm=safe_float(tel["speed"].get("rpm")), steps_per_s=safe_float(tel["speed"].get("steps_per_s")),
            belt_mm_s=safe_float(tel["speed"].get("belt_mm_s")),
            state=str(tel.get("state", "RUNNING")),
            fft_peaks=list(tel.get("fft_peaks", []))[:8],
            band_energy=[safe_float(v) for v in tel.get("acoustic", {}).get("band_energy", [])],
        ))


async def ingest_telemetry(raw: Dict[str, Any], source: str = "mqtt") -> Optional[Dict[str, Any]]:
    """Point d'entrée unique : appelé par le client MQTT ET par le simulateur."""
    try:
        tel = TelemetryPayload.model_validate(raw)
    except ValidationError as exc:
        logger.warning("Payload de télémétrie rejeté (%s) : %s", source, exc.errors()[:2])
        return None

    motor_id = tel.motor_id
    payload = tel.model_dump()
    payload["_source"] = source
    payload["_wear"] = safe_float(raw.get("_wear", 0.0))
    payload["target_speed_rpm"] = safe_float(raw.get("target_speed_rpm", 120.0))
    ts = int(payload["ts"])

    # 1) Buffer circulaire (haute fréquence, pour les graphes temps réel)
    hub.telemetry[motor_id].push_nowait(payload)
    hub.tick_counters(n_telemetry=1, n_bytes=len(str(payload)))
    if payload.get("fft_peaks"):
        ml_service.spectrogram_point(motor_id, payload)
    if source == "sim":
        hub.status["last_sim_ts"] = ts

    # 2) Diffusion temps réel
    await hub.broadcast({"type": "telemetry", "motor_id": motor_id, "data": payload}, motors=[motor_id])

    # 3) Features (1 Hz) : depuis l'edge si fournies, sinon extraites côté serveur
    now = time.time()
    features: Dict[str, float] = {}
    if now - _last_feature_ts.get(motor_id, 0.0) >= 1.0:
        _last_feature_ts[motor_id] = now
        edge_feats = raw.get("features")
        features = (dict(edge_feats) if isinstance(edge_feats, dict) and edge_feats
                    else ml_service.features_from_telemetry(motor_id, payload))
        if features:
            hub.features[motor_id].push_nowait({"ts": ts, "motor_id": motor_id, "features": features,
                                                "state": payload.get("state", "RUNNING")})
            await hub.broadcast({"type": "features", "motor_id": motor_id,
                                 "data": {"ts": ts, "features": features}}, motors=[motor_id])

    # 4) Inférence IA (throttlée) + alertes
    inference = await ml_service.infer(motor_id, payload, features or {}, payload.get("state", "RUNNING"))
    metrics = {
        "vibration_rms": safe_float(payload["vibration"].get("rms")),
        "vibration_peak": safe_float(payload["vibration"].get("peak")),
        "kurtosis": safe_float(payload["vibration"].get("kurtosis"), 3.0),
        "current_rms": safe_float(payload["current"].get("rms_a")),
        "power_w": safe_float(payload["current"].get("power_w")),
        "motor_temp": safe_float(payload["temperature"].get("motor_c")),
        "bearing_temp": safe_float(payload["temperature"].get("bearing_c")),
        "rpm": safe_float(payload["speed"].get("rpm")),
        "spl_db": safe_float(payload["acoustic"].get("spl_db")),
        "belt_speed_error": safe_float(features.get("belt_speed_error")) if features else 0.0,
    }
    if inference:
        metrics["health_index"] = safe_float(inference.get("health_index"), 100.0)
        metrics["anomaly_score"] = safe_float(inference.get("anomaly_score"))
        await alert_engine.evaluate(motor_id, metrics, ts)
        await alert_engine.evaluate_ai(motor_id, inference)
    else:
        await alert_engine.evaluate(motor_id, metrics, ts)

    # 5) Persistance sous-échantillonnée (~1 Hz)
    dt = now - _last_persist_ts.get(motor_id, 0.0)
    if dt >= 1.0 or _last_persist_ts.get(motor_id, 0.0) == 0.0:
        _last_persist_ts[motor_id] = now
        try:
            import asyncio
            await asyncio.to_thread(_persist_telemetry, motor_id, payload)
        except Exception as exc:  # pragma: no cover
            logger.error("Persistance télémétrie échouée (%s) : %s", motor_id, exc)

    return inference


async def ingest_features(raw: Dict[str, Any]) -> None:
    """Features publiées par l'ESP32 sur conveyor/{m}/features."""
    try:
        payload = FeaturePayload.model_validate(raw)
    except ValidationError as exc:
        logger.warning("Features edge rejetées : %s", exc.errors()[:2])
        return
    motor_id = payload.motor_id
    hub.features[motor_id].push_nowait(payload.model_dump())
    await hub.broadcast({"type": "features", "motor_id": motor_id, "data": payload.model_dump()},
                        motors=[motor_id])


async def ingest_heartbeat(raw: Dict[str, Any]) -> None:
    """Heartbeat ESP32 : met à jour la supervision de connectivité."""
    try:
        hb = HeartbeatPayload.model_validate(raw)
    except ValidationError:
        return
    hub.devices[hb.device_id] = {**hb.model_dump(), "status": "ONLINE", "received_at": int(time.time() * 1000)}
    from ..db import session_scope as _scope
    from ..models import Device
    from sqlmodel import select

    def _update() -> None:
        with _scope() as session:
            device = session.exec(select(Device).where(Device.device_id == hb.device_id)).first()
            if device:
                device.last_heartbeat = int(time.time() * 1000)
                device.uptime_s = hb.uptime_s
                device.rssi_dbm = hb.rssi_dbm
                device.firmware = hb.firmware
                device.ip = hb.ip
                device.status = "ONLINE"
                session.add(device)

    # Trois tentatives : une écriture peut échouer sur verrou SQLite quand la
    # télémétrie et les tâches planifiées écrivent en même temps. L'horodatage
    # en mémoire (`hub.devices`) reste de toute façon disponible pour la
    # supervision, qui ne dépend donc jamais d'un succès d'écriture.
    import asyncio
    for attempt in range(3):
        try:
            await asyncio.to_thread(_update)
            break
        except Exception as exc:  # pragma: no cover - dépend de la charge disque
            if attempt == 2:
                logger.warning("Mise à jour device abandonnée après 3 tentatives : %s", exc)
            else:
                await asyncio.sleep(0.2 * (attempt + 1))
    await hub.broadcast({"type": "heartbeat", "data": hub.devices[hb.device_id]})


async def ingest_status(raw: Dict[str, Any]) -> None:
    """État global du convoyeur (topic conveyor/status)."""
    try:
        status = ConveyorStatusPayload.model_validate(raw)
    except ValidationError:
        return
    hub.status.update({
        "running": status.running,
        "speed_rpm": status.target_speed_rpm,
        "belt_mm_s": status.belt_mm_s,
        "load_kg": status.load_kg,
        "parcels_per_min": status.target_parcels_per_min,
        "mode": status.mode,
        "ts": status.ts,
    })
    await hub.broadcast({"type": "status", "data": hub.status})


async def ingest_edge_inference(raw: Dict[str, Any]) -> None:
    """Inférence TinyML publiée par l'ESP32 (comparaison edge vs serveur)."""
    try:
        payload = EdgeInferencePayload.model_validate(raw)
    except ValidationError:
        return
    comparison = ml_service.register_edge_inference(payload.model_dump())
    await hub.broadcast({"type": "edge_inference", "motor_id": payload.motor_id,
                         "data": payload.model_dump(), "comparison": comparison})


async def handle_message(topic: str, payload: Dict[str, Any]) -> None:
    """Routeur de topics MQTT → bon pipeline."""
    prefix = settings.mqtt_topic_prefix
    parts = topic.split("/")
    try:
        if topic.endswith("/telemetry"):
            await ingest_telemetry(payload, source="mqtt")
        elif topic.endswith("/features"):
            await ingest_features(payload)
        elif topic.endswith("/edge_inference"):
            await ingest_edge_inference(payload)
        elif topic.endswith("/heartbeat"):
            await ingest_heartbeat(payload)
        elif topic == f"{prefix}/status":
            await ingest_status(payload)
        elif len(parts) >= 2 and parts[1] in ("M1", "M2", "m1", "m2"):
            # Tolérance : payload sans suffixe de topic → on devine par le contenu
            if "vibration" in payload:
                payload.setdefault("motor_id", parts[1].upper())
                await ingest_telemetry(payload, source="mqtt")
    except Exception as exc:  # pragma: no cover
        logger.exception("Erreur de traitement du topic %s : %s", topic, exc)
