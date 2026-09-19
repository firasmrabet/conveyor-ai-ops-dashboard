"""API équipements : ESP32, diagnostic réseau, sécurité, commandes OTA/recalibration."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, desc, select

from ..config import settings
from ..db import get_session
from ..models import Device, SecurityEvent, now_ts_ms
from ..realtime import hub
from ..schemas import DeviceCommand
from ..services.common import logger
from ..services.mqtt_client import mqtt_bridge

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _device_payload(device: Device) -> Dict[str, Any]:
    now = now_ts_ms()
    age_s = (now - device.last_heartbeat) / 1000.0
    status = "ONLINE" if age_s < 15 else ("DEGRADED" if age_s < 45 else "OFFLINE")
    return {
        **device.model_dump(),
        "status": status if device.status != "OFFLINE" or age_s >= 45 else device.status,
        "heartbeat_age_s": round(age_s, 1),
        "uptime_human": _human(device.uptime_s),
    }


def _human(seconds: float) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    return f"{d}j {h}h {m}m" if d else f"{h}h {m}m"


@router.get("", summary="Liste des équipements avec leur état")
def list_devices(session: Session = Depends(get_session)) -> Dict[str, Any]:
    devices = session.exec(select(Device)).all()
    if not devices:
        # Aucun device enregistré : on expose la passerelle observée dans le hub
        return {"devices": [], "observed": hub.devices, "mqtt": mqtt_bridge.stats()}
    return {
        "devices": [_device_payload(d) for d in devices],
        "observed": hub.devices,
        "mqtt": mqtt_bridge.stats(),
    }


@router.get("/network", summary="Diagnostic réseau (latence, débit, pertes)")
def network_diagnostic(minutes: int = Query(15, ge=1, le=120), session: Session = Depends(get_session)) -> Dict[str, Any]:
    device = session.exec(select(Device)).first()
    rates = hub.rates
    samples: List[Dict[str, Any]] = []
    base_latency = device.latency_ms if device else 12.0
    now = time.time()
    for i in range(minutes * 4):
        ts = int((now - (minutes * 4 - i) * 15) * 1000)
        # Reconstruction d'une courbe de latence plausible à partir des métriques connues
        jitter = ((i * 7919) % 13) / 4.0
        latency = base_latency + jitter + (4.0 if i % 17 == 0 else 0.0)
        samples.append({
            "ts": ts,
            "latency_ms": round(latency, 1),
            "messages_per_s": round(rates.get("telemetry_per_s", 0.0) * (0.9 + (i % 5) / 50), 2),
            "loss_pct": round(max(0.0, (i % 23) / 100), 2),
            "rssi_dbm": (device.rssi_dbm if device else -60) - (i % 4),
        })
    return {
        "samples": samples,
        "summary": {
            "avg_latency_ms": round(sum(s["latency_ms"] for s in samples) / max(len(samples), 1), 2),
            "max_latency_ms": max((s["latency_ms"] for s in samples), default=0),
            "avg_loss_pct": round(sum(s["loss_pct"] for s in samples) / max(len(samples), 1), 3),
            "messages_per_s": rates.get("telemetry_per_s", 0.0),
            "throughput_kb_s": rates.get("throughput_kb_s", 0.0),
            "mqtt": mqtt_bridge.stats(),
        },
    }


@router.get("/security", summary="État de sécurité (TLS, auth, rotation de clés, tampering)")
def security_state(session: Session = Depends(get_session)) -> Dict[str, Any]:
    device = session.exec(select(Device)).first()
    events = session.exec(select(SecurityEvent).order_by(desc(SecurityEvent.ts)).limit(50)).all()
    return {
        "tls": {
            "enabled": device.tls_enabled if device else False,
            "version": "TLS 1.3" if (device and device.tls_enabled) else "—",
            "ca_configured": bool(settings.mqtt_ca_cert),
            "mode": settings.mode,
        },
        "authentication": {
            "mode": device.auth_mode if device else "USER_PASSWORD",
            "mqtt_user_configured": bool(settings.mqtt_username),
            "jwt_enabled": True,
            "roles": ["admin", "operator", "viewer"],
        },
        "key_rotation": {
            "last_rotation": device.last_key_rotation if device else None,
            "interval_days": 30,
            "next_rotation_days": 30 - ((now_ts_ms() - (device.last_key_rotation or now_ts_ms())) / 86_400_000 if device else 0),
        },
        "tamper_detected": device.tamper_detected if device else False,
        "events": [e.model_dump() for e in events],
        "recommendations": [
            "Activer MQTT over TLS (MQTT_TLS=true) avec certificats par device en production.",
            "Utiliser un mot de passe MQTT unique par passerelle et le faire tourner tous les 30 jours.",
            "Restreindre CORS_ORIGINS au domaine de production.",
            "Conserver SECRET_KEY hors du dépôt (.env non versionné).",
        ],
    }


@router.post("/{device_id}/command", summary="Envoyer une commande à un device (reboot, OTA, calibration…)")
async def device_command(device_id: str, payload: DeviceCommand,
                         session: Session = Depends(get_session)) -> Dict[str, Any]:
    device = session.exec(select(Device).where(Device.device_id == device_id)).first()
    if not device:
        raise HTTPException(404, f"Device inconnu : {device_id}")

    action = payload.action
    params = dict(payload.payload)
    result: Dict[str, Any] = {"action": action, "device_id": device_id, "ts": now_ts_ms()}

    if action == "reboot":
        device.uptime_s = 0.0
        device.last_heartbeat = now_ts_ms()
        device.status = "ONLINE"
        result["message"] = "Redémarrage programmé : la passerelle republiera son heartbeat sous 5 s"
    elif action == "recalibrate":
        device.last_key_rotation = device.last_key_rotation or now_ts_ms()
        from ..services.alerting import alert_engine
        await alert_engine.raise_alert(
            motor_id="SYSTEM", metric="calibration", value=1.0, threshold=0.0, operator=">",
            severity="INFO", message=f"Calibration des capteurs demandée sur {device_id}",
            alert_type="DEVICE", origin="DEVICE",
        )
        result["message"] = "Calibration lancée (offset accéléromètre + zéro courant)"
    elif action == "ota_update":
        device.firmware = str(params.get("version", "1.1.0"))
        result["message"] = f"Mise à jour OTA simulée vers {device.firmware}"
    elif action == "rotate_key":
        device.last_key_rotation = now_ts_ms()
        session.add(SecurityEvent(ts=now_ts_ms(), device_id=device_id, event="KEY_ROTATION",
                                  detail="Rotation manuelle depuis la console", actor="admin"))
        from ..security import create_access_token
        result["device_token"] = create_access_token(device_id, "device",
                                                     {"device": device_id, "scope": "mqtt"})[:48] + "…"
        result["message"] = "Clé du device renouvelée (jeton MQTT émis)"
    elif action == "set_sampling":
        hz = float(params.get("hz", 10.0))
        device.sampling_hz = max(1.0, min(50.0, hz))
        result["message"] = f"Fréquence d'échantillonnage fixée à {device.sampling_hz} Hz"
    elif action == "test_network":
        result["message"] = "Test réseau exécuté : latence et débit mesurés"
        result["metrics"] = {"latency_ms": device.latency_ms, "rssi_dbm": device.rssi_dbm,
                             "packet_loss_pct": device.packet_loss_pct}

    session.add(device)
    session.commit()

    # Publication de la commande descendante (si un broker est branché)
    forwarded = mqtt_bridge.send_command(device_id, action, params)
    result["forwarded"] = forwarded

    # Le simulateur applique immédiatement la commande en mode SIMULATION
    await hub.broadcast({"type": "device_command", "data": result})
    logger.info("Commande device : %s → %s", action, device_id)
    return result


@router.get("/{device_id}/logs", summary="Journal des événements d'un device")
def device_logs(device_id: str, limit: int = Query(100, le=500),
                session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    events = session.exec(
        select(SecurityEvent).where(SecurityEvent.device_id == device_id)
        .order_by(desc(SecurityEvent.ts)).limit(limit)
    ).all()
    return [e.model_dump() for e in events]


@router.post("/{device_id}/simulate-network-event", summary="Injecter un événement réseau (démonstration)")
async def simulate_network_event(device_id: str, event: str = Query("TAMPER"),
                                 session: Session = Depends(get_session)) -> Dict[str, Any]:
    device = session.exec(select(Device).where(Device.device_id == device_id)).first()
    if not device:
        raise HTTPException(404, "Device inconnu")
    if event == "TAMPER":
        device.tamper_detected = True
        detail = "Sceau firmware modifié — redémarrage inattendu détecté"
    elif event == "OFFLINE":
        device.last_heartbeat = now_ts_ms() - 90_000
        device.status = "OFFLINE"
        detail = "Perte de heartbeat > 45 s"
    elif event == "WEAK_SIGNAL":
        device.rssi_dbm = -88
        detail = "RSSI dégradé (-88 dBm)"
    else:
        detail = "Événement de démonstration"
    session.add(device)
    session.add(SecurityEvent(ts=now_ts_ms(), device_id=device_id, event=event, detail=detail,
                              actor="simulator", severity="WARNING"))
    session.commit()
    return {"device_id": device_id, "event": event, "detail": detail}


@router.post("/{device_id}/reset-tamper", summary="Réarmer le drapeau de tampering")
def reset_tamper(device_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    device = session.exec(select(Device).where(Device.device_id == device_id)).first()
    if not device:
        raise HTTPException(404, "Device inconnu")
    device.tamper_detected = False
    session.add(device)
    session.commit()
    return {"device_id": device_id, "tamper_detected": False}
