"""Extraction des features (domaine temporel + fréquentiel) — côté serveur et edge."""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# Features exploitées par les modèles (ordre stable = contrat d'interface)
FEATURE_NAMES: List[str] = [
    "vibration_rms", "vibration_peak", "kurtosis", "crest_factor",
    "h1_ratio", "h2_ratio", "hf_ratio", "band_hf_energy",
    "current_rms", "current_peak", "power_w", "current_ripple",
    "motor_temp", "bearing_temp", "temp_delta",
    "rpm", "speed_error", "rpm_variance", "belt_speed_error",
    "spl_db", "load_estimate",
]

# --- Baseline "moteur sain" : référence pour la normalisation et le radar ---
# Valeurs mesurées sur le banc simulé au régime nominal (120 rpm, charge 0,6 kg,
# régime thermique établi) : elles servent de référence pour les z-scores, le radar
# « signature actuelle vs saine » et le modèle heuristique de secours.
BASELINE: Dict[str, float] = {
    "vibration_rms": 0.068,
    "vibration_peak": 0.195,
    "kurtosis": 2.30,
    "crest_factor": 2.70,
    "h1_ratio": 1.19,
    "h2_ratio": 0.31,
    "hf_ratio": 0.170,
    "band_hf_energy": 0.170,
    "current_rms": 0.73,
    "current_peak": 0.88,
    "power_w": 8.80,
    "current_ripple": 0.145,
    "motor_temp": 42.2,
    "bearing_temp": 43.8,
    "temp_delta": 17.8,
    "rpm": 120.0,
    "speed_error": 0.5,
    "rpm_variance": 0.01,
    "belt_speed_error": 0.9,
    "spl_db": 60.8,
    "load_estimate": 0.73,
}

# --- Tolérances : écart considéré comme "normal" (±) ---
TOLERANCE: Dict[str, float] = {
    "vibration_rms": 0.045,
    "vibration_peak": 0.15,
    "kurtosis": 0.80,
    "crest_factor": 0.90,
    "h1_ratio": 0.55,
    "h2_ratio": 0.30,
    "hf_ratio": 0.30,
    "band_hf_energy": 0.30,
    "current_rms": 0.18,
    "current_peak": 0.25,
    "power_w": 2.30,
    "current_ripple": 0.05,
    "motor_temp": 6.0,
    "bearing_temp": 6.5,
    "temp_delta": 6.0,
    "rpm": 3.0,
    "speed_error": 2.0,
    "rpm_variance": 0.05,
    "belt_speed_error": 3.0,
    "spl_db": 3.0,
    "load_estimate": 0.25,
}

# --- Directions "dégradation" : +1 = la hausse est mauvaise, -1 = la baisse ---
DIRECTION: Dict[str, int] = {
    "rpm": -1, "belt_speed_error": 1, "temp_delta": 1,
}

# Fréquences de défauts de roulement (multiplicateurs de la fréquence de rotation)
BEARING_MULTS = {"BPFO": 4.8, "BPFI": 7.2, "BSF": 2.4, "FTF": 0.4}


def rms(x: Sequence[float]) -> float:
    arr = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(arr ** 2))) if arr.size else 0.0


def kurtosis(x: Sequence[float]) -> float:
    arr = np.asarray(x, dtype=np.float64)
    if arr.size < 4:
        return 3.0
    m = arr.mean()
    var = np.mean((arr - m) ** 2)
    if var < 1e-12:
        return 3.0
    return float(np.mean((arr - m) ** 4) / (var ** 2))


def crest_factor(x: Sequence[float]) -> float:
    arr = np.asarray(x, dtype=np.float64)
    r = rms(arr)
    return float(np.max(np.abs(arr)) / r) if r > 1e-9 else 0.0


def spectrum(signal: Sequence[float], fs: float) -> Tuple[np.ndarray, np.ndarray]:
    """Spectre d'amplitude corrigé (fenêtre de Hann)."""
    x = np.asarray(signal, dtype=np.float64)
    if x.size < 16:
        return np.array([0.0]), np.array([0.0])
    x = x - x.mean()
    win = np.hanning(x.size)
    amp = np.abs(np.fft.rfft(x * win)) * (2.0 / np.sum(win))
    freqs = np.fft.rfftfreq(x.size, d=1.0 / fs)
    return freqs, amp


def harmonic_ratios(freqs: np.ndarray, amp: np.ndarray, rpm: float) -> Dict[str, float]:
    """Ratios 1x / 2x / HF par rapport à l'énergie totale du spectre."""
    f_rot = max(rpm, 1.0) / 60.0
    total = float(np.sum(amp ** 2)) + 1e-12

    def _band(center: float, width_ratio: float = 0.12) -> float:
        lo, hi = center * (1 - width_ratio), center * (1 + width_ratio)
        m = (freqs >= lo) & (freqs <= hi)
        return float(np.sum(amp[m] ** 2))

    h1 = _band(f_rot)
    h2 = _band(2 * f_rot)
    hf = float(np.sum(amp[(freqs > 500) & (freqs < 3000)] ** 2))
    return {
        "h1_ratio": round(h1 / total, 4),
        "h2_ratio": round(h2 / total, 4),
        "hf_ratio": round(hf / total, 4),
        "band_hf_energy": round(hf, 6),
    }


def bearing_frequencies(rpm: float) -> Dict[str, float]:
    f_rot = max(rpm, 1.0) / 60.0
    return {name: round(mult * f_rot, 2) for name, mult in BEARING_MULTS.items()}


def rolling_features(samples: Sequence[float], fs: float, rpm: float) -> Dict[str, float]:
    """Features temps + fréquence sur une fenêtre d'échantillons (edge ou serveur)."""
    arr = np.asarray(samples, dtype=np.float64)
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    r = rms(arr)
    feats = {
        "vibration_rms": round(r, 4),
        "vibration_peak": round(peak, 4),
        "kurtosis": round(kurtosis(arr), 3),
        "crest_factor": round(peak / r, 3) if r > 1e-9 else 0.0,
    }
    freqs, amp = spectrum(arr, fs)
    feats.update(harmonic_ratios(freqs, amp, rpm))
    return feats


def extract_from_telemetry(telemetry: Dict, prev: Optional[Dict] = None,
                           rpm_window: Optional[List[float]] = None) -> Dict[str, float]:
    """Construit le vecteur de features à partir d'un payload de télémétrie.

    C'est le chemin utilisé en mode HARDWARE (l'ESP32 n'envoie pas les features)
    comme en mode SIMULATION interne.
    """
    vib = telemetry.get("vibration", {})
    cur = telemetry.get("current", {})
    temp = telemetry.get("temperature", {})
    speed = telemetry.get("speed", {})
    aco = telemetry.get("acoustic", {})

    rpm = float(speed.get("rpm", 0.0))
    target_rpm = float(telemetry.get("target_speed_rpm", BASELINE["rpm"]))
    belt = float(speed.get("belt_mm_s", 0.0))
    expected_belt = target_rpm / 60.0 * 42.0  # 42 mm par tour de poulie
    power = float(cur.get("power_w", 0.0))
    motor_t = float(temp.get("motor_c", 0.0))
    bearing_t = float(temp.get("bearing_c", 0.0))
    ambient = float(temp.get("ambient_c", 25.0))

    hf = abs(float(vib.get("rms", 0.0))) * abs(float(vib.get("crest_factor", 1.0)))
    rpm_window = (rpm_window or [])[-60:]
    # Variance de vitesse plancher : évite qu'un bruit numérique négligeable
    # ne devienne un signal fort une fois standardisé.
    rpm_var = max(float(np.var(rpm_window)), 5e-4) if len(rpm_window) > 2 else 5e-4

    # FFT : on isole l'amplitude maximale dans une fenêtre ÉTROITE autour de 1x et 2x.
    # (une fenêtre large ferait fuiter le pic 2x vers le 1x et inversement — biais majeur)
    peaks = telemetry.get("fft_peaks") or []
    f_rot = max(rpm, 1.0) / 60.0

    def _peak_in(target: float) -> float:
        window = max(0.35, 0.10 * target)
        amplitudes = [float(p.get("amp", 0.0))
                      for p in peaks if abs(float(p.get("freq", 0.0)) - target) <= window]
        return max(amplitudes) if amplitudes else 0.0

    h1 = _peak_in(f_rot)
    h2 = _peak_in(2 * f_rot)
    rms_ref = max(float(vib.get("rms", 0.0)), 1e-4)
    total_amp = sum(float(p.get("amp", 0.0)) ** 2 for p in peaks) + 1e-9  # énergie totale (spectrogramme)

    load_est = float(np.clip((power / 12.0), 0, 2.0)) if power else 0.5

    feats = {
        "vibration_rms": round(float(vib.get("rms", 0.0)), 4),
        "vibration_peak": round(float(vib.get("peak", 0.0)), 4),
        "kurtosis": round(float(vib.get("kurtosis", 3.0)), 3),
        "crest_factor": round(float(vib.get("crest_factor", 0.0)), 3),
        # Ratios d'ordre normalisés par la vibration globale : robustes au nombre de
        # pics renvoyés par la carte (le panier « top 6 » change d'une trame à l'autre,
        # une normalisation par la somme de ce panier rendait les ratios instables).
        "h1_ratio": round(h1 / rms_ref, 3),
        "h2_ratio": round(h2 / rms_ref, 3),
        "hf_ratio": round(min(1.0, hf), 4),
        "band_hf_energy": round(hf, 6),
        "current_rms": round(float(cur.get("rms_a", 0.0)), 4),
        "current_peak": round(float(cur.get("peak_a", 0.0)), 4),
        "power_w": round(power, 3),
        "current_ripple": round(abs(float(cur.get("peak_a", 0.0)) - float(cur.get("rms_a", 0.0))), 4),
        "motor_temp": round(motor_t, 2),
        "bearing_temp": round(bearing_t, 2),
        "temp_delta": round(bearing_t - ambient, 2),
        "rpm": round(rpm, 2),
        "speed_error": round(abs(target_rpm - rpm), 2),
        "rpm_variance": round(max(rpm_var, 5e-4), 5),
        "belt_speed_error": round(abs(expected_belt - belt), 2),
        "spl_db": round(float(aco.get("spl_db", 0.0)), 2),
        "load_estimate": round(load_est, 3),
    }
    return feats


def deviation(features: Dict[str, float], name: str) -> float:
    """Écart normalisé (0 = nominal, 1 = à la limite de tolérance, >1 = dépassement)."""
    base = BASELINE.get(name, 0.0)
    tol = max(TOLERANCE.get(name, 1.0), 1e-9)
    value = float(features.get(name, base))
    direction = DIRECTION.get(name, 1)
    return float(np.clip(direction * (value - base) / tol, -6, 6))
