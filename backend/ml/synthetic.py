"""Génération du jeu de données synthétique « physiquement réaliste ».

Utilisé par :
- `ml/train.py` (entraînement des modèles),
- `scripts/seed_db.py` (données de démonstration, 24 h d'historique),
- tests de non-régression du pipeline IA.

Chaque échantillon = une fenêtre de vibration simulée → vecteur de features
(+ label de défaut + RUL cible en heures).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from .features import FEATURE_NAMES, extract_from_telemetry
from .physics import FAULT_TYPES, MotorPhysics, estimate_spl

# RUL cible (heures) par défaut et par intensité
RUL_BY_FAULT: Dict[str, float] = {
    "NONE": 4200.0,
    "BEARING_FAULT": 320.0,
    "UNBALANCE": 1100.0,
    "MISALIGNMENT": 900.0,
    "BELT_SLIP": 480.0,
    "OVERLOAD": 260.0,
    "OVERHEATING": 180.0,
    "SENSOR_FAULT": 2000.0,
    "MOTOR_STALL": 0.0,   # identifié côté edge (le contact de rotation est perdu)
}


def telemetry_from_physics(physics: MotorPhysics, now: float, dt: float,
                           target_rpm: float = 120.0) -> Dict:
    """Fait avancer la physique d'un pas et retourne un payload de télémétrie complet."""
    vib, cur = physics.generate_block(dt, now)
    # Indicateurs extraits de la fenêtre d'analyse complète (stable, cf. MotorPhysics.window_stats)
    stats = physics.window_stats()
    if len(physics._buffer) and np.allclose(physics._buffer, 0):  # pragma: no cover - sécurité
        stats = physics.block_stats(vib, cur)
    temp_motor, temp_bearing = physics.update_thermal(dt, stats["current_rms"], now)
    gaps = max(0.55, 0.001 * max(12.0 - physics.load_kg * 1.6, 0.6)) * target_rpm
    belt = physics.rpm / 60.0 * 42.0
    faults = physics._faults(now)
    power = round(stats["current_rms"] * 12.0 * (0.55 + 0.45 * physics.rpm / max(target_rpm, 1.0)), 2)

    return {
        "device_id": "esp32-conveyor-01",
        "motor_id": physics.motor_id,
        "ts": int(now * 1000),
        "vibration": {
            "ax": round(float(vib[-1]), 4),
            "ay": round(float(vib[-2]), 4),
            "az": round(1.0 + float(np.std(vib)), 4),
            "rms": stats["rms"],
            "peak": stats["peak"],
            "kurtosis": stats["kurtosis"],
            "crest_factor": stats["crest_factor"],
        },
        "fft_peaks": physics.fft_peaks(6),
        "current": {
            "rms_a": stats["current_rms"],
            "peak_a": stats["current_peak"],
            "power_w": power,
        },
        "temperature": {
            "motor_c": round(temp_motor, 2),
            "bearing_c": round(temp_bearing, 2),
            "ambient_c": round(physics.ambient_c, 2),
        },
        "acoustic": {
            "spl_db": estimate_spl(stats["rms"], physics.rpm, faults["BELT_SLIP"], faults["OVERLOAD"]),
            "band_energy": physics.band_energy([(20, 200), (200, 800), (800, 2500), (2500, 8000)]),
        },
        "speed": {
            "rpm": round(physics.rpm, 2),
            "steps_per_s": round(physics.rpm / 60.0 * gaps, 1),
            "belt_mm_s": round(belt, 2),
        },
        "target_speed_rpm": target_rpm,
        "state": "FAULT" if physics.fault.fault not in ("NONE",) else ("RUNNING" if physics.running else "STOPPED"),
    }


def generate_dataset(samples_per_class: int = 120, seed: int = 7, warmup: int = 80,
                     fault_types: Optional[List[str]] = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Retourne (X, y_class, y_rul) avec X ∈ R^(n×len(FEATURE_NAMES))."""
    rng = np.random.default_rng(seed)
    fault_types = fault_types or ["NONE"] + [f for f in FAULT_TYPES if f not in ("NONE", "SENSOR_FAULT", "MOTOR_STALL")]
    X: List[List[float]] = []
    y: List[str] = []
    rul: List[float] = []

    for fault in fault_types:
        for i in range(samples_per_class):
            load = float(rng.uniform(0.0, 1.6))
            target_rpm = float(rng.uniform(80, 150))
            intensity = 0.0 if fault == "NONE" else float(rng.uniform(8, 100))
            phys = MotorPhysics(
                motor_id="M1",
                target_rpm=target_rpm,
                load_kg=load,
                ambient_c=float(rng.uniform(20, 30)),
                seed=int(rng.integers(0, 10_000)),
            )
            phys.inject_fault(fault if fault != "NONE" else "NONE", intensity, degradation_rate=0.0, now=0.0)
            now = 0.0
            # dt = 0.125 s ⇒ fenêtre de vitesse identique à celle du pipeline temps réel
            dt = 0.125
            telemetry: Dict = {}
            rpm_window: List[float] = []
            # Le moteur est amené à son régime thermique permanent AVANT l'extraction :
            # on évite ainsi d'entraîner un modèle sur des échantillons « moteur froid ».
            phys.rpm = target_rpm
            phys.settle_thermal(current_rms=0.28 + 0.55 * load + 0.9 * (intensity / 100.0) * 0.5,
                                seconds=900.0, dt=15.0)
            # Usure résiduelle et diversité mécanique : un banc réel n'est jamais
            # parfaitement nominal (jeu de bande, alignement, graissage, ambiance).
            phys.wear = float(rng.uniform(0.0, 0.18))
            healthy_slip = 0.0   # dérive lente de glissement (processus AR(1))
            for step in range(warmup + 2):
                now += dt
                telemetry = telemetry_from_physics(phys, now, dt, target_rpm)
                # Consigne réellement transmise par la carte : identique au régime visé
                telemetry["target_speed_rpm"] = target_rpm
                # Glissement sain de la bande (charge mécanique, jeu de poulie) : le
                # moteur suit sa consigne à quelques dixièmes de tr/min près, jamais
                # exactement. L'écart est un processus lent (dérive de tension) et non
                # un saut par échantillon : c'est ce que l'on observe sur un vrai banc,
                # et cela évite d'élargir artificiellement la classe « sain ».
                healthy_slip = 0.9 * healthy_slip + 0.1 * float(rng.normal(0, 0.18))
                if rng.random() < 0.35:
                    telemetry["speed"]["rpm"] = round(telemetry["speed"]["rpm"]
                                                      - abs(healthy_slip) * float(rng.uniform(0.8, 1.8)), 2)
                rpm_window.append(telemetry["speed"]["rpm"])
            feats = extract_from_telemetry(telemetry, None, rpm_window)
            X.append([feats.get(n, 0.0) for n in FEATURE_NAMES])
            y.append(fault if fault != "NONE" else "HEALTHY")
            base_rul = RUL_BY_FAULT.get(fault, 1500.0)
            rul.append(max(0.5, base_rul * (1.0 - 0.92 * (intensity / 100.0)) * (1.0 - 0.18 * load)
                           * (1.0 + 0.12 * (1.0 - target_rpm / 150.0))))
    return np.array(X, dtype=np.float64), np.array(y), np.array(rul, dtype=np.float64)


def generate_demo_series(motor_id: str = "M1", hours: float = 24.0, step_s: float = 60.0,
                         fault: str = "NONE", fault_at_hour: float = 8.0,
                         intensity: float = 60.0, seed: int = 11,
                         base_rpm: float = 120.0) -> List[Dict]:
    """Série longue (par défaut 24 h) pour le seed de démonstration et l'analytics.

    Retourne une liste de payloads de télémétrie espacés de `step_s`.
    """
    phys = MotorPhysics(motor_id=motor_id, target_rpm=base_rpm, load_kg=0.6, seed=seed)
    series: List[Dict] = []
    now = 0.0
    n = int(hours * 3600 / step_s)
    rpm_window: List[float] = []
    for i in range(n):
        hour = i * step_s / 3600.0
        if fault != "NONE" and hour >= fault_at_hour and not phys.fault.active:
            phys.inject_fault(fault, intensity, degradation_rate=0.4, now=now)
        now += step_s
        telemetry = telemetry_from_physics(phys, now, step_s, base_rpm)
        # Cycle réaliste : arrêts de nuit, redémarrages
        if (hour % 8) > 7.6:
            phys.set_running(False)
        else:
            phys.set_running(True)
        telemetry["ts"] = int((now - hours * 3600 + hours * 3600) * 1000)
        rpm_window.append(telemetry["speed"]["rpm"])
        telemetry["features"] = extract_from_telemetry(telemetry, None, rpm_window[-30:])
        telemetry["hour"] = round(hour, 3)
        series.append(telemetry)
    return series
