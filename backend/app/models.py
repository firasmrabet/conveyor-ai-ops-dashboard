"""Modèles de données persistés (SQLModel)."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Index
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlmodel import Field, SQLModel

# JSON portable (SQLite + PostgreSQL) : on stocke des dict/list Python.
JSONType = SQLiteJSON


def now_ts_ms() -> int:
    return int(time.time() * 1000)


class Motor(SQLModel, table=True):
    """Les deux moteurs du convoyeur (M1 gauche, M2 droit)."""

    id: str = Field(primary_key=True, description="Identifiant métier : M1 / M2")
    name: str = Field(default="")
    label: str = Field(default="")          # ex : "Moteur GAUCHE"
    side: str = Field(default="LEFT")       # LEFT / RIGHT
    model: str = Field(default="NEMA17-42HD4027")
    nominal_current_a: float = 1.2
    nominal_rpm: float = 120.0
    nominal_temp_c: float = 45.0
    baseline: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONType))
    installed_at: datetime = Field(default_factory=datetime.utcnow)
    last_maintenance: Optional[datetime] = None
    maintenance_count: int = 0
    running_hours: float = 0.0


class TelemetryRecord(SQLModel, table=True):
    """Télémétrie brute (sous-échantillonnée à l'ingestion pour la rétention)."""

    __tablename__ = "telemetry"
    __table_args__ = (
        Index("ix_telemetry_motor_ts", "motor_id", "ts"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: int = Field(default_factory=now_ts_ms, index=True)
    motor_id: str = Field(index=True)
    device_id: str = Field(default="esp32-conveyor-01")

    # Vibration
    vib_ax: float = 0.0
    vib_ay: float = 0.0
    vib_az: float = 0.0
    vib_rms: float = 0.0
    vib_peak: float = 0.0
    kurtosis: float = 3.0
    crest_factor: float = 0.0

    # Courant / puissance
    current_rms: float = 0.0
    current_peak: float = 0.0
    power_w: float = 0.0

    # Températures
    temp_motor_c: float = 0.0
    temp_bearing_c: float = 0.0
    temp_ambient_c: float = 0.0

    # Acoustique
    spl_db: float = 0.0

    # Cinématique
    rpm: float = 0.0
    steps_per_s: float = 0.0
    belt_mm_s: float = 0.0

    state: str = Field(default="RUNNING")
    fft_peaks: List[dict] = Field(default_factory=list, sa_column=Column(JSONType))
    band_energy: List[float] = Field(default_factory=list, sa_column=Column(JSONType))


class FeatureRecord(SQLModel, table=True):
    """Features calculées (1 Hz) — alimente l'IA et l'historique long terme."""

    __tablename__ = "features"
    __table_args__ = (Index("ix_features_motor_ts", "motor_id", "ts"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: int = Field(default_factory=now_ts_ms, index=True)
    motor_id: str = Field(index=True)
    features: Dict[str, float] = Field(default_factory=dict, sa_column=Column(JSONType))
    spectral: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONType))
    state: str = Field(default="RUNNING")


class InferenceRecord(SQLModel, table=True):
    """Sortie du pipeline IA (backend) ou de l'edge (TinyML)."""

    __tablename__ = "inference"
    __table_args__ = (Index("ix_inference_motor_ts", "motor_id", "ts"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: int = Field(default_factory=now_ts_ms, index=True)
    motor_id: str = Field(index=True)
    source: str = Field(default="server")  # server | edge
    health_index: float = 100.0
    anomaly_score: float = 0.0
    is_anomaly: bool = False
    fault_class: str = "HEALTHY"
    fault_probabilities: Dict[str, float] = Field(default_factory=dict, sa_column=Column(JSONType))
    rul_hours: float = 0.0
    rul_low: float = 0.0
    rul_high: float = 0.0
    explanation: List[dict] = Field(default_factory=list, sa_column=Column(JSONType))
    model_version: str = "v0"
    inference_ms: float = 0.0


class HealthSnapshot(SQLModel, table=True):
    """Photo périodique de l'état santé (agrégats, MTBF, heatmap)."""

    __tablename__ = "health_snapshot"
    __table_args__ = (Index("ix_health_motor_ts", "motor_id", "ts"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: int = Field(default_factory=now_ts_ms, index=True)
    motor_id: str = Field(index=True)
    health_index: float = 100.0
    anomaly_score: float = 0.0
    state: str = "RUNNING"
    fault_class: str = "HEALTHY"
    vibration_rms: float = 0.0
    current_rms: float = 0.0
    temp_bearing_c: float = 0.0
    rpm: float = 0.0


class HourlyAggregate(SQLModel, table=True):
    """Agrégat horaire (rétention longue, heatmap, tendances)."""

    __tablename__ = "hourly_aggregate"
    __table_args__ = (Index("ix_hourly_motor_bucket", "motor_id", "bucket_ts"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    bucket_ts: int = Field(index=True)  # début d'heure (ms)
    motor_id: str = Field(index=True)
    avg_health: float = 0.0
    min_health: float = 100.0
    avg_vibration_rms: float = 0.0
    max_vibration_rms: float = 0.0
    avg_current_rms: float = 0.0
    avg_temp_bearing: float = 0.0
    max_temp_bearing: float = 0.0
    avg_rpm: float = 0.0
    anomaly_count: int = 0
    sample_count: int = 0


class Alert(SQLModel, table=True):
    """Alerte générée par le moteur de règles ou par l'IA."""

    __tablename__ = "alert"
    __table_args__ = (Index("ix_alert_motor_ts", "motor_id", "ts"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: int = Field(default_factory=now_ts_ms, index=True)
    motor_id: str = Field(index=True)
    severity: str = Field(default="WARNING")   # INFO | WARNING | CRITICAL
    type: str = Field(default="THRESHOLD")     # THRESHOLD | ANOMALY | AI_CLASS | DEVICE | SYSTEM
    origin: str = Field(default="RULES")       # RULES | ML | EDGE | DEVICE
    metric: str = Field(default="")
    value: float = 0.0
    threshold: float = 0.0
    message: str = ""
    status: str = Field(default="NEW")         # NEW | ACKNOWLEDGED | RESOLVED
    rule_id: Optional[int] = None
    assigned_to: str = ""
    comment: str = ""
    work_order_id: Optional[int] = None
    acknowledged_by: str = ""
    acknowledged_at: Optional[int] = None
    resolved_at: Optional[int] = None
    context: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONType))


class AlertRule(SQLModel, table=True):
    """Règle de seuil configurable (avec hystérésis, durée et anti-spam)."""

    __tablename__ = "alert_rule"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    motor_id: str = Field(default="*")  # * = tous
    metric: str                          # vibration_rms, bearing_temp, current_rms, health_index, rpm...
    operator: str = Field(default=">")   # > >= < <=
    threshold: float
    hysteresis: float = 0.0
    duration_s: float = 0.0              # dépassement continu requis
    debounce_s: float = 60.0             # délai minimum entre deux alertes identiques
    severity: str = Field(default="WARNING")
    enabled: bool = True
    channels: List[str] = Field(default_factory=lambda: ["inapp"], sa_column=Column(JSONType))
    description: str = ""


class Device(SQLModel, table=True):
    """ESP32 et autres passerelles."""

    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: str = Field(index=True, unique=True)
    name: str = ""
    ip: str = "192.168.1.42"
    mac: str = ""
    rssi_dbm: int = -62
    firmware: str = "1.0.0"
    uptime_s: float = 0.0
    last_heartbeat: int = Field(default_factory=now_ts_ms)
    status: str = "ONLINE"            # ONLINE | OFFLINE | DEGRADED
    power_source: str = "USB 5V"
    battery_pct: float = 100.0
    sampling_hz: float = 10.0
    msgs_per_s: float = 0.0
    packet_loss_pct: float = 0.0
    latency_ms: float = 12.0
    tls_enabled: bool = False
    auth_mode: str = "USER_PASSWORD"
    last_key_rotation: Optional[int] = None
    tamper_detected: bool = False
    ota_channel: str = "stable"
    metrics: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONType))


class SecurityEvent(SQLModel, table=True):
    __tablename__ = "security_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: int = Field(default_factory=now_ts_ms, index=True)
    device_id: str = Field(default="")
    event: str = ""          # AUTH_OK | AUTH_FAIL | TLS_HANDSHAKE | KEY_ROTATION | TAMPER | OTA
    detail: str = ""
    actor: str = "system"
    severity: str = "INFO"


class WorkOrder(SQLModel, table=True):
    __tablename__ = "work_order"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: int = Field(default_factory=now_ts_ms, index=True)
    motor_id: str = ""
    title: str = ""
    description: str = ""
    priority: str = "MEDIUM"      # LOW | MEDIUM | HIGH | CRITICAL
    status: str = "OPEN"          # OPEN | PLANNED | IN_PROGRESS | DONE
    source: str = "AI"            # AI | MANUAL | RULE
    assigned_to: str = ""
    estimated_hours: float = 1.0
    parts: List[str] = Field(default_factory=list, sa_column=Column(JSONType))
    due_in_days: float = 5.0
    alert_id: Optional[int] = None


class Intervention(SQLModel, table=True):
    """Journal des interventions (horodaté + signé)."""

    __tablename__ = "intervention"

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: int = Field(default_factory=now_ts_ms, index=True)
    motor_id: str = ""
    work_order_id: Optional[int] = None
    technician: str = ""
    action: str = ""
    description: str = ""
    duration_min: float = 30.0
    parts_used: List[str] = Field(default_factory=list, sa_column=Column(JSONType))
    cost_eur: float = 0.0
    signature: str = ""   # SHA-256(payload + serversecret) => journal inaltérable


class User(SQLModel, table=True):
    __tablename__ = "app_user"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    full_name: str = ""
    role: str = "operator"        # admin | operator | viewer
    password_hash: str = ""
    created_at: int = Field(default_factory=now_ts_ms)
    last_login: Optional[int] = None
    active: bool = True


class ModelVersion(SQLModel, table=True):
    __tablename__ = "model_version"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = "conveyor-anomaly"
    version: str = "v1.0.0"
    kind: str = "server"          # server | edge
    trained_at: int = Field(default_factory=now_ts_ms)
    size_kb: float = 0.0
    latency_ms: float = 0.0
    accuracy: float = 0.0
    f1: float = 0.0
    rul_mae: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    roc_auc: float = 0.0
    active: bool = False
    notes: str = ""
    metrics: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONType))


class AppSetting(SQLModel, table=True):
    __tablename__ = "app_setting"

    key: str = Field(primary_key=True)
    value: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONType))
    updated_at: int = Field(default_factory=now_ts_ms)
