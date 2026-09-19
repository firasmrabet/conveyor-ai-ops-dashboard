"""Configuration centralisée (pydantic-settings, lue depuis .env)."""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Application ---
    app_name: str = "Conveyor AI Ops"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # Liste séparée par des virgules : "http://a:3000,http://b:3000"
    # (conservée en str pour éviter le parsing JSON implicite de pydantic-settings)
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    secret_key: str = "dev-secret-key-change-me"
    access_token_expire_minutes: int = 720
    rate_limit_per_minute: int = 600

    # --- Mode ---
    mode: str = "SIMULATION"  # SIMULATION | HARDWARE | HYBRID
    simulate_on_m1: bool = True
    simulate_on_m2: bool = True
    sim_hz: float = 8.0
    sim_publish_mqtt: bool = False

    # --- DB ---
    database_url: str = "sqlite:///./data/conveyor.db"
    db_echo: bool = False

    # --- MQTT ---
    mqtt_enabled: bool = False
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_tls: bool = False
    mqtt_ca_cert: str = ""
    mqtt_client_id: str = "conveyor-backend"
    mqtt_topic_prefix: str = "conveyor"
    mqtt_qos: int = 1

    # --- Redis ---
    redis_enabled: bool = False
    redis_url: str = "redis://redis:6379/0"

    # --- ML ---
    ml_artifacts_dir: str = "./artifacts"
    ml_active_bundle: str = "conveyor-synth"
    ml_model_version: str = "v1.4.0"
    ml_run_inference_hz: float = 1.0
    rul_critical_health: float = 40.0
    # Anti-flapping des alertes IA : N inférences consécutives concordantes + temporisation
    ai_confirmations: int = 3
    ai_debounce_s: float = 90.0
    anomaly_confirmations: int = 3
    anomaly_debounce_s: float = 120.0

    # --- Alerting ---
    alert_email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "alerts@conveyor.local"
    alert_webhook_enabled: bool = False
    webhook_url: str = ""
    alert_sound_enabled: bool = True

    # --- Rétention ---
    raw_retention_days: int = 7
    feature_retention_days: int = 90

    # --- Rapport ---
    report_company: str = "Projet IoT & Edge AI"
    report_author: str = "Cellule Maintenance Prédictive"

    @property
    def cors_origin_list(self) -> List[str]:
        """Origines CORS autorisées (liste)."""
        if not self.cors_origins:
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_simulation(self) -> bool:
        return self.mode.upper() in {"SIMULATION", "HYBRID"}

    @property
    def is_hardware(self) -> bool:
        return self.mode.upper() in {"HARDWARE", "HYBRID"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
