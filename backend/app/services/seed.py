"""
Amorçage de la base au démarrage.

Garantit qu'un `docker compose up` (ou un `uvicorn` local) présente immédiatement
un système cohérent et démonstratif :

* 2 moteurs (M1 gauche, M2 droite) avec leur baseline capteurs,
* 3 utilisateurs (admin / opérateur / invité) avec mots de passe hachés,
* la passerelle ESP32 avec ses informations réseau et de sécurité,
* les 9 règles d'alerte par défaut (hystérésis + anti-spam),
* un historique de 24 h (santé, télémétrie, inférences) généré physiquement,
* des interventions, ordres de travail et alertes passés,
* les enveloppes d'amorçage des buffers temps réel (courbes non vides dès l'écran d'accueil).
"""
from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, List

from sqlmodel import Session, select

from ..config import settings
from ..db import engine
from ..models import (Alert, AlertRule, AppSetting, Device, HealthSnapshot, HourlyAggregate, InferenceRecord,
                      Intervention, ModelVersion, Motor, SecurityEvent, TelemetryRecord, User, WorkOrder, now_ts_ms)
from ..realtime import hub
from ..security import hash_password
from .common import logger

MOTOR_LABELS: Dict[str, str] = {"M1": "Moteur GAUCHE", "M2": "Moteur DROIT"}
MOTOR_SIDES: Dict[str, str] = {"M1": "LEFT", "M2": "RIGHT"}

# Baselines capteurs nominales (utilisées par les z-scores et les comparaisons radar)
M1_BASELINE = {
    "vibration_rms": 0.082, "vibration_peak": 0.31, "kurtosis": 3.05,
    "current_rms": 0.94, "power_w": 11.3, "motor_temp_c": 41.5, "bearing_temp_c": 38.9,
    "ambient_temp_c": 24.0, "spl_db": 52.4, "rpm": 120.0,
}
M2_BASELINE = {
    "vibration_rms": 0.086, "vibration_peak": 0.33, "kurtosis": 3.08,
    "current_rms": 0.97, "power_w": 11.7, "motor_temp_c": 42.2, "bearing_temp_c": 39.4,
    "ambient_temp_c": 24.2, "spl_db": 52.9, "rpm": 120.4,
}

DEFAULT_USERS = [
    ("admin@conveyor.local", "Admin Conveyor", "admin", "admin123"),
    ("operateur@conveyor.local", "Opérateur Ligne 1", "operator", "operateur123"),
    ("invite@conveyor.local", "Invité Démo", "viewer", "invite123"),
]


# ---------------------------------------------------------------------------
def seed_all(force: bool = False) -> Dict[str, Any]:
    """Amorce la base. Idempotent : ne recrée que ce qui manque."""
    created: Dict[str, Any] = {"motors": 0, "users": 0, "rules": 0, "devices": 0,
                              "history_points": 0, "alerts": 0, "interventions": 0, "work_orders": 0}
    with Session(engine) as session:
        created["motors"] = _seed_motors(session)
        created["users"] = _seed_users(session)
        created["rules"] = _seed_rules(session)
        created["devices"] = _seed_devices(session)
        created["settings"] = _seed_settings(session)
        created["models"] = _seed_models(session)

        has_history = session.exec(select(HealthSnapshot).limit(1)).first() is not None
        if force or not has_history:
            stats = _seed_history(session)
            created.update(stats)
        session.commit()
    _seed_realtime_buffers()
    logger.info("Seed terminé : %s", created)
    return created


# ---------------------------------------------------------------------------
def _seed_motors(session: Session) -> int:
    count = 0
    for motor_id in ("M1", "M2"):
        if session.get(Motor, motor_id):
            continue
        baseline = M1_BASELINE if motor_id == "M1" else M2_BASELINE
        session.add(Motor(
            id=motor_id,
            name=f"Moteur {motor_id}",
            label=MOTOR_LABELS[motor_id],
            side=MOTOR_SIDES[motor_id],
            model="NEMA17-42HD4027 (pas à pas bipolaire)",
            nominal_current_a=1.2,
            nominal_rpm=120.0,
            nominal_temp_c=45.0,
            baseline=dict(baseline),
            maintenance_count=2 if motor_id == "M1" else 1,
            running_hours=412.5 if motor_id == "M1" else 368.0,
        ))
        count += 1
    return count


def _seed_users(session: Session) -> int:
    count = 0
    for email, name, role, password in DEFAULT_USERS:
        existing = session.exec(select(User).where(User.email == email)).first()
        if existing:
            continue
        session.add(User(email=email, full_name=name, role=role,
                         password_hash=hash_password(password), active=True))
        count += 1
    return count


def _seed_rules(session: Session) -> int:
    """Reprend les 9 règles par défaut du moteur ML (hystérésis + durée + anti-spam)."""
    from ml.model import DEFAULT_RULES  # source unique de vérité (utilisée aussi par ml/train.py)
    count = 0
    for rule in DEFAULT_RULES:
        exists = session.exec(select(AlertRule).where(AlertRule.name == rule["name"])).first()
        if exists:
            continue
        session.add(AlertRule(**rule))
        count += 1
    return count


def _seed_devices(session: Session) -> int:
    if session.exec(select(Device)).first():
        return 0
    session.add(Device(
        device_id="esp32-conveyor-01",
        name="Passerelle ESP32 — Banc tapis roulant",
        ip="192.168.1.42",
        mac="AC:67:B2:1F:44:9E",
        rssi_dbm=-58,
        firmware="1.0.0",
        uptime_s=3600.0 * 6.4,
        status="ONLINE",
        power_source="USB-C 5V / 2A",
        battery_pct=100.0,
        sampling_hz=10.0,
        msgs_per_s=16.0,
        packet_loss_pct=0.02,
        latency_ms=11.4,
        tls_enabled=False,
        auth_mode="USER_PASSWORD",
        last_key_rotation=now_ts_ms() - 11 * 86_400_000,
        tamper_detected=False,
        ota_channel="stable",
        metrics={"free_heap_kb": 178.5, "wifi_channel": 6, "cpu_mhz": 240, "core_temp_c": 48.3},
    ))
    session.add(SecurityEvent(ts=now_ts_ms() - 3600_000, device_id="esp32-conveyor-01", event="AUTH_OK",
                              detail="Connexion MQTT authentifiée (user_password)", actor="device"))
    session.add(SecurityEvent(ts=now_ts_ms() - 86_400_000, device_id="esp32-conveyor-01", event="TLS_HANDSHAKE",
                              detail="Handshake TLS 1.2 (mode test) — certificat non validé", actor="device",
                              severity="INFO"))
    session.add(SecurityEvent(ts=now_ts_ms() - 11 * 86_400_000, device_id="esp32-conveyor-01",
                              event="KEY_ROTATION", detail="Rotation automatique du mot de passe MQTT",
                              actor="scheduler"))
    return 1


def _seed_settings(session: Session) -> int:
    defaults: Dict[str, Any] = {
        "units": {"temperature": "C", "vibration": "g", "speed": "rpm", "power": "W"},
        "language": "fr",
        "theme": "dark",
        "notifications": {"inapp": True, "sound": True, "email": False, "webhook": False},
        "thresholds": {"health_warning": 55, "health_critical": 30, "vibration_warning": 0.35,
                       "vibration_critical": 0.55, "temp_warning": 65, "temp_critical": 75},
        "reduced_motion": False,
        "retention_days": {"raw": settings.raw_retention_days, "features": settings.feature_retention_days},
    }
    count = 0
    for key, value in defaults.items():
        if session.get(AppSetting, key):
            continue
        session.add(AppSetting(key=key, value=value if isinstance(value, dict) else {"value": value}))
        count += 1
    return count


def _seed_models(session: Session) -> int:
    """Déclare les versions de modèles dans le registre (serveur + embarqué)."""
    count = 0
    definitions = [
        dict(name=settings.ml_active_bundle, version=settings.ml_model_version, kind="server",
             accuracy=0.9340, f1=0.9325, rul_mae=326.4, precision=0.941, recall=0.928,
             roc_auc=0.9846, size_kb=1692.0, latency_ms=6.4, active=True,
             notes="RandomForest + Isolation Forest + régression RUL entraînés sur données synthétiques physiques."),
        dict(name="conveyor-anomaly", version="v1.2.0", kind="server",
             accuracy=0.9182, f1=0.9094, rul_mae=362.8, precision=0.921, recall=0.905,
             roc_auc=0.9761, size_kb=1655.0, latency_ms=6.9, active=False,
             notes="Version précédente — conservée pour comparaison et rollback."),
        dict(name="conveyor-tinyml", version="v1.0.3", kind="edge",
             accuracy=0.8710, f1=0.8620, rul_mae=0.0, precision=0.884, recall=0.861,
             roc_auc=0.9120, size_kb=42.0, latency_ms=1.8, active=True,
             notes="Modèle int8 (42 Ko) exécuté sur l'ESP32 : détection saine/anormale + score heuristique."),
    ]
    for definition in definitions:
        exists = session.exec(
            select(ModelVersion).where(ModelVersion.name == definition["name"],
                                       ModelVersion.version == definition["version"])
        ).first()
        if exists:
            continue
        session.add(ModelVersion(trained_at=now_ts_ms() - 86_400_000 * 3, metrics={}, **definition))
        count += 1
    return count


# ---------------------------------------------------------------------------
#  Historique de démonstration (24 h) — généré avec la physique du banc
# ---------------------------------------------------------------------------
def _seed_history(session: Session, hours: int = 24, step_min: int = 10) -> Dict[str, int]:
    """
    Crée l'historique : snapshots de santé, télémétrie agrégée, inférences,
    alertes, interventions, ordres de travail et agrégats horaires.

    La chronologie est scénarisée pour raconter une histoire crédible :
    ligne nominale, dérive de roulement sur M2 (détectée → alerte → intervention),
    puis retour au nominal.
    """
    rng = random.Random(2024)
    now = now_ts_ms()
    steps = int(hours * 60 / step_min)
    stats = {"history_points": 0, "alerts": 0, "interventions": 0, "work_orders": 0}

    # Incident scénarisé : M2 roulement en dérive entre -14 h et -9 h
    incident_start = now - 14 * 3600_000
    incident_end = now - 9 * 3600_000
    intervention_ts = incident_end + 1800_000

    work_order = WorkOrder(
        created_at=incident_start + 2 * 3600_000,
        motor_id="M2",
        title="Contrôle du roulement moteur M2",
        description="Dérive de l'indice de santé conjuguée à une hausse du kurtosis et de la température roulement. "
                    "Vibration RMS > 0,30 g sur 40 minutes consécutives.",
        priority="HIGH", status="DONE", source="AI", assigned_to="Opérateur Ligne 1",
        estimated_hours=1.5, parts=["Roulement 608-2RS", "Graisse haute température"], due_in_days=2.0,
    )
    session.add(work_order)
    session.flush()
    stats["work_orders"] += 1

    alert_incident = Alert(
        ts=incident_start + 2 * 3600_000 - 600_000, motor_id="M2", severity="CRITICAL", type="AI_CLASS",
        origin="ML", metric="health_index", value=41.8, threshold=55.0,
        message="Défaut de roulement détecté par l'IA sur M2 (santé 41,8/100, RUL estimé 96 h)",
        status="RESOLVED", assigned_to="Opérateur Ligne 1",
        comment="Roulement remplacé et regraissé, vibration revenue au nominal.",
        work_order_id=work_order.id, acknowledged_by="admin@conveyor.local",
        acknowledged_at=incident_start + 2 * 3600_000, resolved_at=intervention_ts,
        context={"fault_class": "BEARING_FAULT", "anomaly_score": 0.71, "rul_hours": 96.0},
    )
    session.add(alert_incident)
    session.add(Alert(
        ts=incident_start + 1_800_000, motor_id="M2", severity="WARNING", type="THRESHOLD", origin="RULES",
        metric="vibration_rms", value=0.281, threshold=0.25,
        message="Vibration RMS supérieure au seuil d'avertissement sur M2 (0,281 g > 0,250 g)",
        status="ACKNOWLEDGED", acknowledged_by="operateur@conveyor.local",
        acknowledged_at=incident_start + 1_900_000, work_order_id=work_order.id,
    ))
    session.add(Alert(
        ts=now - 3 * 3600_000, motor_id="M1", severity="INFO", type="SYSTEM", origin="DEVICE",
        metric="heartbeat", value=1.0, threshold=0.0,
        message="Passerelle esp32-conveyor-01 reconnectée après 2 s de coupure réseau",
        status="RESOLVED", resolved_at=now - 3 * 3600_000 + 60_000,
    ))
    stats["alerts"] += 3

    intervention = Intervention(
        ts=intervention_ts, motor_id="M2", work_order_id=work_order.id, technician="Opérateur Ligne 1",
        action="Remplacement du roulement 608-2RS + graissage haute température",
        description="Roulement déposé (jeu axial anormal), remplacement, alignement poulie vérifié au comparateur, "
                    "test de rotation à 150 rpm sans charge puis à pleine charge.",
        duration_min=95.0, parts_used=["Roulement 608-2RS", "Graisse haute température"], cost_eur=78.5,
    )
    session.add(intervention)
    session.add(Intervention(
        ts=now - 26 * 3600_000, motor_id="M1", technician="Opérateur Ligne 1",
        action="Contrôle périodique — tension de bande et serrage des vis moteur",
        description="Contrôle préventif mensuel : tension de bande ajustée (+3 mm), visserie contrôlée au couple.",
        duration_min=35.0, parts_used=[], cost_eur=0.0,
    ))
    stats["interventions"] += 2

    snapshots: List[HealthSnapshot] = []
    telemetries: List[TelemetryRecord] = []
    inferences: List[InferenceRecord] = []

    for index in range(steps + 1):
        ts = now - (steps - index) * step_min * 60_000
        for motor_id in ("M1", "M2"):
            baseline = M1_BASELINE if motor_id == "M1" else M2_BASELINE
            phase = index / max(steps, 1)
            health = 96.0 + rng.uniform(-1.6, 1.6)
            vibration = baseline["vibration_rms"] * rng.uniform(0.94, 1.07)
            current = baseline["current_rms"] * rng.uniform(0.95, 1.06)
            temp_bearing = baseline["bearing_temp_c"] + rng.uniform(-1.2, 1.2)
            temp_motor = baseline["motor_temp_c"] + rng.uniform(-1.2, 1.2)
            rpm = baseline["rpm"] + rng.uniform(-0.6, 0.6)
            fault_class, anomaly = "HEALTHY", rng.uniform(0.02, 0.14)
            state = "RUNNING"

            # Pause déjeuner : ligne arrêtée une heure en milieu de fenêtre
            if 0.42 < phase < 0.46:
                state, rpm, vibration = "STOPPED", 0.0, baseline["vibration_rms"] * 0.18
                current = baseline["current_rms"] * 0.16

            # Incident roulement M2
            if motor_id == "M2" and incident_start <= ts <= incident_end:
                progress = (ts - incident_start) / max(incident_end - incident_start, 1)
                severity = math.sin(progress * math.pi)  # montée puis résolution
                health = 92.0 - 52.0 * severity
                vibration = baseline["vibration_rms"] * (1.0 + 3.4 * severity)
                temp_bearing = baseline["bearing_temp_c"] + 11.0 * severity
                temp_motor = baseline["motor_temp_c"] + 5.5 * severity
                current = baseline["current_rms"] * (1.0 + 0.22 * severity)
                anomaly = 0.18 + 0.55 * severity
                if severity > 0.35:
                    fault_class = "BEARING_FAULT"

            snapshots.append(HealthSnapshot(
                ts=ts, motor_id=motor_id, health_index=round(health, 2), anomaly_score=round(anomaly, 3),
                state=state, fault_class=fault_class, vibration_rms=round(vibration, 4),
                current_rms=round(current, 4), temp_bearing_c=round(temp_bearing, 2), rpm=round(rpm, 2),
            ))
            if index % 3 == 0:  # télémétrie persistée toutes les 30 min
                telemetries.append(TelemetryRecord(
                    ts=ts, motor_id=motor_id, vib_ax=round(vibration * 0.62, 5), vib_ay=round(vibration * 0.71, 5),
                    vib_az=round(vibration * 0.34, 5), vib_rms=round(vibration, 4),
                    vib_peak=round(vibration * 3.6, 4), kurtosis=round(3.0 + anomaly * 4.2, 3),
                    crest_factor=round(vibration * 3.6 / max(vibration, 1e-6), 3),
                    current_rms=round(current, 4), current_peak=round(current * 1.42, 4),
                    power_w=round(current * 12.0, 2), temp_motor_c=round(temp_motor, 2),
                    temp_bearing_c=round(temp_bearing, 2), temp_ambient_c=round(baseline["ambient_temp_c"] + rng.uniform(-0.8, 0.8), 2),
                    spl_db=round(baseline["spl_db"] + 9.0 * anomaly, 2), rpm=round(rpm, 2),
                    steps_per_s=round(rpm / 60 * 200, 2), belt_mm_s=round(rpm / 60 * 157.0, 2),
                    state=state, fft_peaks=[{"freq": round(rpm / 60, 2), "amp": round(vibration, 4)}],
                    band_energy=[round(vibration * 0.4, 5), round(vibration * 0.9, 5), round(vibration * 0.5, 5)],
                ))
            inferences.append(InferenceRecord(
                ts=ts, motor_id=motor_id, source="server", health_index=round(health, 2),
                anomaly_score=round(anomaly, 3), is_anomaly=anomaly > 0.6, fault_class=fault_class,
                fault_probabilities={fault_class: round(max(anomaly, 0.5), 3), "HEALTHY": round(1 - max(anomaly, 0.5), 3)},
                rul_hours=round(max(60.0, 600.0 * (health / 100) ** 2.4), 1),
                rul_low=round(max(40.0, 460.0 * (health / 100) ** 2.4), 1),
                rul_high=round(max(90.0, 780.0 * (health / 100) ** 2.4), 1),
                explanation=[{"feature": "kurtosis", "label": "Kurtosis vibration", "contribution": round(anomaly, 3),
                              "deviation": round(anomaly * 2.5, 2)}],
                model_version=settings.ml_model_version, inference_ms=round(rng.uniform(3.0, 9.0), 2),
            ))

    # Agrégats horaires (utilisés par les tendances longues)
    bucket: Dict[tuple[str, int], List[HealthSnapshot]] = {}
    for snapshot in snapshots:
        bucket.setdefault((snapshot.motor_id, snapshot.ts // 3600_000 * 3600_000), []).append(snapshot)
    for (motor_id, bucket_ts), rows in bucket.items():
        session.add(HourlyAggregate(
            bucket_ts=bucket_ts, motor_id=motor_id,
            avg_health=round(sum(r.health_index for r in rows) / len(rows), 2),
            min_health=round(min(r.health_index for r in rows), 2),
            avg_vibration_rms=round(sum(r.vibration_rms for r in rows) / len(rows), 4),
            max_vibration_rms=round(max(r.vibration_rms for r in rows), 4),
            avg_current_rms=round(sum(r.current_rms for r in rows) / len(rows), 4),
            avg_temp_bearing=round(sum(r.temp_bearing_c for r in rows) / len(rows), 2),
            max_temp_bearing=round(max(r.temp_bearing_c for r in rows), 2),
            avg_rpm=round(sum(r.rpm for r in rows) / len(rows), 2),
            anomaly_count=sum(1 for r in rows if r.anomaly_score > 0.55),
            sample_count=len(rows),
        ))

    for rows in (snapshots, telemetries, inferences):
        session.add_all(rows)
    stats["history_points"] = len(snapshots) + len(telemetries) + len(inferences)
    return stats


# ---------------------------------------------------------------------------
def _seed_realtime_buffers(frames: int = 40) -> None:
    """
    Pré-remplit les buffers temps réel avec quelques secondes de télémétrie
    synthétique : les graphiques temps réel ne sont jamais vides au premier rendu.
    """
    rng = random.Random(7)
    now = now_ts_ms()
    for motor_id in ("M1", "M2"):
        baseline = M1_BASELINE if motor_id == "M1" else M2_BASELINE
        for index in range(frames):
            ts = now - (frames - index) * 125  # 8 Hz
            jitter = rng.uniform(-1.0, 1.0)
            telemetry = {
                "device_id": "esp32-conveyor-01", "motor_id": motor_id, "ts": ts,
                "vibration": {"ax": round(baseline["vibration_rms"] * 0.6 + jitter * 0.004, 5),
                              "ay": round(baseline["vibration_rms"] * 0.7 + jitter * 0.004, 5),
                              "az": round(baseline["vibration_rms"] * 0.33 + jitter * 0.003, 5),
                              "rms": round(baseline["vibration_rms"] * (1 + jitter * 0.04), 5),
                              "peak": round(baseline["vibration_peak"] * (1 + jitter * 0.05), 5),
                              "kurtosis": round(baseline["kurtosis"] + jitter * 0.05, 3),
                              "crest_factor": round(3.6 + jitter * 0.12, 3)},
                "fft_peaks": [{"freq": 2.0, "amp": round(baseline["vibration_rms"] * 0.8, 4)},
                              {"freq": 4.0, "amp": round(baseline["vibration_rms"] * 0.35, 4)},
                              {"freq": 6.0, "amp": round(baseline["vibration_rms"] * 0.18, 4)}],
                "current": {"rms_a": round(baseline["current_rms"] * (1 + jitter * 0.03), 4),
                            "peak_a": round(baseline["current_rms"] * 1.42, 4),
                            "power_w": round(baseline["current_rms"] * 12.0, 2)},
                "temperature": {"motor_c": round(baseline["motor_temp_c"] + jitter * 0.3, 2),
                                "bearing_c": round(baseline["bearing_temp_c"] + jitter * 0.3, 2),
                                "ambient_c": round(baseline["ambient_temp_c"] + jitter * 0.15, 2)},
                "acoustic": {"spl_db": round(baseline["spl_db"] + jitter * 0.5, 2),
                             "band_energy": [0.014, 0.021, 0.009]},
                "speed": {"rpm": round(baseline["rpm"] * (1 + jitter * 0.004), 2),
                          "steps_per_s": round(baseline["rpm"] / 60 * 200, 2),
                          "belt_mm_s": round(baseline["rpm"] / 60 * 157.0, 2)},
                "state": "RUNNING",
            }
            hub.telemetry[motor_id].push_nowait(telemetry)
        # Quelques inférences pour peupler la courbe de santé
        for index in range(6):
            health = 95.6 + rng.uniform(-0.9, 0.9)
            inference = {
                "motor_id": motor_id, "ts": now - (6 - index) * 1000,
                "health_index": round(health, 2), "anomaly_score": round(rng.uniform(0.03, 0.12), 3),
                "is_anomaly": False, "fault_class": "HEALTHY",
                "fault_probabilities": {"HEALTHY": 0.94, "BEARING_FAULT": 0.02, "UNBALANCE": 0.02,
                                        "MISALIGNMENT": 0.01, "BELT_SLIP": 0.005, "OVERLOAD": 0.003,
                                        "OVERHEATING": 0.002},
                "rul_hours": round(900.0 + rng.uniform(-40, 40), 1),
                "rul_confidence": {"low": 780.0, "high": 1120.0},
                "explanation": [{"feature": "kurtosis", "label": "Kurtosis vibration",
                                 "contribution": round(rng.uniform(0.02, 0.1), 3), "deviation": 0.2}],
                "model_version": settings.ml_model_version, "inference_ms": round(rng.uniform(3.0, 9.0), 2),
            }
            hub.inference[motor_id].push_nowait(inference)
