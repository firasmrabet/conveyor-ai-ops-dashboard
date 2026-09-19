"""Schémas Pydantic : validation stricte de TOUS les payloads entrants/sortants.

Les structures `TelemetryPayload` et `InferencePayload` respectent au caractère
près le protocole MQTT du cahier des charges.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

FAULT_CLASSES = [
    "HEALTHY",
    "BEARING_FAULT",
    "UNBALANCE",
    "MISALIGNMENT",
    "BELT_SLIP",
    "OVERLOAD",
    "OVERHEATING",
]


# --------------------------------------------------------------------------
#  Télémétrie
# --------------------------------------------------------------------------
class VibrationBlock(BaseModel):
    ax: float = 0.0
    ay: float = 0.0
    az: float = 0.0
    rms: float = 0.0
    peak: float = 0.0
    kurtosis: float = 3.0
    crest_factor: float = 0.0


class FftPeak(BaseModel):
    freq: float
    amp: float


class CurrentBlock(BaseModel):
    rms_a: float = 0.0
    peak_a: float = 0.0
    power_w: float = 0.0


class TemperatureBlock(BaseModel):
    motor_c: float = 0.0
    bearing_c: float = 0.0
    ambient_c: float = 0.0


class AcousticBlock(BaseModel):
    spl_db: float = 0.0
    band_energy: List[float] = Field(default_factory=list)


class SpeedBlock(BaseModel):
    rpm: float = 0.0
    steps_per_s: float = 0.0
    belt_mm_s: float = 0.0


class TelemetryPayload(BaseModel):
    """Payload publié par l'ESP32 sur conveyor/{m1|m2}/telemetry."""

    device_id: str = "esp32-conveyor-01"
    motor_id: Literal["M1", "M2"]
    ts: int
    vibration: VibrationBlock = Field(default_factory=VibrationBlock)
    fft_peaks: List[FftPeak] = Field(default_factory=list)
    current: CurrentBlock = Field(default_factory=CurrentBlock)
    temperature: TemperatureBlock = Field(default_factory=TemperatureBlock)
    acoustic: AcousticBlock = Field(default_factory=AcousticBlock)
    speed: SpeedBlock = Field(default_factory=SpeedBlock)
    state: Literal["RUNNING", "STOPPED", "FAULT", "CALIBRATING", "UNKNOWN"] = "RUNNING"

    @field_validator("fft_peaks")
    @classmethod
    def _limit_peaks(cls, v: List[FftPeak]) -> List[FftPeak]:
        return sorted(v, key=lambda p: -p.amp)[:16]


class FeaturePayload(BaseModel):
    """Payload publié sur conveyor/{m1|m2}/features (features edge, 1 Hz)."""

    device_id: str = "esp32-conveyor-01"
    motor_id: Literal["M1", "M2"]
    ts: int
    features: Dict[str, float] = Field(default_factory=dict)
    window_s: float = 1.0


class EdgeInferencePayload(BaseModel):
    """Payload publié sur conveyor/{m1|m2}/edge_inference (TinyML embarqué)."""

    device_id: str = "esp32-conveyor-01"
    motor_id: Literal["M1", "M2"]
    ts: int
    health_index: float = 100.0
    anomaly_score: float = 0.0
    is_anomaly: bool = False
    fault_class: str = "HEALTHY"
    model_version: str = "tiny-v1"
    inference_ms: float = 3.0


class ConveyorStatusPayload(BaseModel):
    device_id: str = "esp32-conveyor-01"
    ts: int
    running: bool = True
    target_speed_rpm: float = 120.0
    belt_mm_s: float = 85.0
    load_kg: float = 0.5
    target_parcels_per_min: float = 12.0
    mode: str = "AUTO"


class HeartbeatPayload(BaseModel):
    device_id: str = "esp32-conveyor-01"
    ts: int
    uptime_s: float = 0.0
    rssi_dbm: int = -60
    firmware: str = "1.0.0"
    ip: str = "192.168.1.42"
    free_heap_kb: float = 120.0


# --------------------------------------------------------------------------
#  Inférence IA (généré par le backend)
# --------------------------------------------------------------------------
class ExplanationItem(BaseModel):
    feature: str
    contribution: float
    # Champs enrichis (libellé lisible + écart normalisé) : conservés par la validation
    label: Optional[str] = None
    deviation: Optional[float] = None


class InferencePayload(BaseModel):
    """Structure exacte exigée par le cahier des charges."""

    motor_id: Literal["M1", "M2"]
    ts: int
    health_index: float
    anomaly_score: float
    is_anomaly: bool
    fault_class: str
    fault_probabilities: Dict[str, float]
    rul_hours: float
    rul_confidence: Dict[str, float]
    explanation: List[ExplanationItem]
    model_version: str
    inference_ms: float


# --------------------------------------------------------------------------
#  Auth
# --------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: Dict[str, str]


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str = ""
    role: Literal["admin", "operator", "viewer"] = "operator"


# --------------------------------------------------------------------------
#  Alertes / règles / maintenance
# --------------------------------------------------------------------------
class AlertUpdate(BaseModel):
    status: Optional[Literal["NEW", "ACKNOWLEDGED", "RESOLVED"]] = None
    assigned_to: Optional[str] = None
    comment: Optional[str] = None


class RuleCreate(BaseModel):
    name: str
    motor_id: str = "*"
    metric: str
    operator: Literal[">", ">=", "<", "<="] = ">"
    threshold: float
    hysteresis: float = 0.0
    duration_s: float = 0.0
    debounce_s: float = 60.0
    severity: Literal["INFO", "WARNING", "CRITICAL"] = "WARNING"
    enabled: bool = True
    channels: List[str] = ["inapp"]
    description: str = ""


class WorkOrderCreate(BaseModel):
    motor_id: str
    title: str
    description: str = ""
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    assigned_to: str = ""
    estimated_hours: float = 1.0
    parts: List[str] = []
    due_in_days: float = 5.0
    alert_id: Optional[int] = None


class InterventionCreate(BaseModel):
    """Intervention de maintenance.

    `motor_id` est facultatif : lorsqu'il est omis, c'est l'identifiant du chemin
    (`POST /api/motors/{motor_id}/interventions`) qui fait foi. Les deux écritures
    restent acceptées pour ne pas casser les intégrations existantes.
    """

    motor_id: Optional[str] = None
    work_order_id: Optional[int] = None
    technician: str = "opérateur"
    action: str
    description: str = ""
    duration_min: float = 30.0
    parts_used: List[str] = []
    cost_eur: float = 0.0


# --------------------------------------------------------------------------
#  Simulateur
# --------------------------------------------------------------------------
class SimulatorCommand(BaseModel):
    running: Optional[bool] = None
    speed_rpm: Optional[float] = None
    load_kg: Optional[float] = None
    parcels_per_min: Optional[float] = None
    ramp_s: float = 2.0


class FaultInjection(BaseModel):
    motor_id: Literal["M1", "M2"]
    fault: Literal[
        "BEARING_FAULT", "UNBALANCE", "MISALIGNMENT", "BELT_SLIP",
        "OVERLOAD", "OVERHEATING", "SENSOR_FAULT", "MOTOR_STALL", "NONE",
    ]
    intensity: float = Field(default=50.0, ge=0, le=100)
    degradation_rate: float = Field(default=1.0, ge=0, le=10)
    auto_clear_after_s: Optional[float] = None


class DemoScenario(BaseModel):
    name: str = "demo-3min"
    duration_s: float = 180.0


# --------------------------------------------------------------------------
#  Devices
# --------------------------------------------------------------------------
class DeviceCommand(BaseModel):
    action: Literal["reboot", "recalibrate", "ota_update", "rotate_key", "set_sampling", "test_network"]
    payload: Dict[str, float | str | bool] = {}


class SettingsUpdate(BaseModel):
    values: Dict[str, object]
