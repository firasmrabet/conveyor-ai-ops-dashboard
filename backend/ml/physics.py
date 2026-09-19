"""Modèle physique du convoyeur à bande (2 moteurs pas à pas).

Ce module est la SOURCE UNIQUE de vérité pour la simulation :
- `scripts/esp32_simulator.py` l'utilise pour publier sur MQTT,
- `app/services/simulator.py` l'utilise en mode SIMULATION interne,
- `ml/synthetic.py` l'utilise pour fabriquer le jeu d'entraînement.

Il produit des signaux physiquement cohérents : sinusoïdes 1x/2x/3x de la
rotation, impulsions de roulement modulées en amplitude, bruit gaussien,
modèle thermique du premier ordre, couple/courant dépendant de la charge.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

FS = 1000.0          # fréquence d'échantillonnage interne (Hz)
BLOCK = 125          # échantillons générés par tick (8 Hz -> 1000 Hz)
BUFFER = 2048        # taille du buffer glissant pour la FFT (df ≈ 0,49 Hz)

FAULT_TYPES = [
    "NONE", "BEARING_FAULT", "UNBALANCE", "MISALIGNMENT",
    "BELT_SLIP", "OVERLOAD", "OVERHEATING", "SENSOR_FAULT", "MOTOR_STALL",
]

# Fréquences caractéristiques de roulement (multiplicateurs de la fréquence de rotation)
BPFO_MULT = 4.8   # bague extérieure
BPFI_MULT = 7.2   # bague intérieure
BSF_MULT = 2.4    # bille
FTF_MULT = 0.4    # cage


@dataclass
class FaultState:
    """Défaut actif sur un moteur, avec intensité et vitesse de dégradation."""

    fault: str = "NONE"
    intensity: float = 0.0          # 0 → 1 (converti depuis 0-100 %)
    degradation_rate: float = 1.0   # 0 → 10 (multiplicateur de vitesse de dégénérescence)
    auto_clear_after_s: Optional[float] = None
    started_at: float = 0.0

    @property
    def active(self) -> bool:
        return self.fault != "NONE" and self.intensity > 0.001


@dataclass
class MotorPhysics:
    """Simulateur d'un moteur pas à pas instrumenté (accéléromètre, courant, température)."""

    motor_id: str = "M1"
    target_rpm: float = 120.0
    load_kg: float = 0.5
    nominal_current: float = 1.2
    nominal_rpm: float = 120.0
    ambient_c: float = 26.0
    running: bool = True
    seed: int = 42

    # État interne
    fault: FaultState = field(default_factory=FaultState)
    rpm: float = 0.0
    temp_motor: float = 26.0
    temp_bearing: float = 26.5
    uptime_s: float = 0.0
    running_hours: float = 0.0
    wear: float = 0.0            # usure cumulée (0 → 1) : dérive lente de la signature
    _rng: np.random.Generator = field(default=None, repr=False)  # type: ignore
    _buffer: np.ndarray = field(default=None, repr=False)        # type: ignore
    _current_buffer: np.ndarray = field(default=None, repr=False)  # type: ignore
    _phase: float = 0.0
    _last_spike_t: float = -1.0
    _speed_noise: float = 0.0
    _sensor_frozen: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)
        self.rpm = self.target_rpm if self.running else 0.0
        # Buffer initialisé avec un signal nominal réel : évite une première seconde de
        # features aberrantes (bruit pur) qui déclencherait de fausses alertes au démarrage.
        self._buffer = self._rng.normal(0, 0.01, BUFFER).astype(np.float32)
        self._current_buffer = (self.nominal_current * 0.3
                                + self._rng.normal(0, 0.02, BUFFER)).astype(np.float32)
        t = np.arange(BUFFER, dtype=np.float64) / FS
        f_rot = max(self.rpm, 1.0) / 60.0
        nominal = (0.045 + 0.06 * self.load_kg) * np.sin(2 * np.pi * f_rot * t) \
            + 0.020 * np.sin(2 * np.pi * 2 * f_rot * t + 1.1) \
            + 0.010 * np.sin(2 * np.pi * 3 * f_rot * t + 0.7)
        self._buffer = (nominal + self._rng.normal(0, 0.032, BUFFER)).astype(np.float32)
        self._current_buffer = (self.nominal_current * 0.35 + 0.1 * self.load_kg
                                + self._rng.normal(0, 0.03, BUFFER)).astype(np.float32)

    # ------------------------------------------------------------------
    #  Commandes
    # ------------------------------------------------------------------
    def inject_fault(self, fault: str, intensity: float = 50.0, degradation_rate: float = 1.0,
                     auto_clear_after_s: Optional[float] = None, now: float = 0.0) -> None:
        self.fault = FaultState(
            fault=fault if fault in FAULT_TYPES else "NONE",
            intensity=float(np.clip(intensity / 100.0, 0, 1)),
            degradation_rate=degradation_rate,
            auto_clear_after_s=auto_clear_after_s,
            started_at=now,
        )
        if fault == "SENSOR_FAULT":
            # Capteur figé : on gèle la dernière valeur connue (signature réaliste)
            self._sensor_frozen = {"rms": float(np.sqrt(np.mean(self._buffer ** 2)))}

    def clear_fault(self) -> None:
        self.fault = FaultState()
        self._sensor_frozen = {}

    def set_running(self, running: bool) -> None:
        self.running = running

    # ------------------------------------------------------------------
    #  Défauts dérivés (intensité effective après dégradation)
    # ------------------------------------------------------------------
    def _effective_intensity(self, now: float) -> float:
        if not self.fault.active:
            return 0.0
        elapsed = max(0.0, now - self.fault.started_at)
        grown = self.fault.intensity + 0.02 * self.fault.degradation_rate * elapsed
        return float(np.clip(grown, 0, 1.0))

    def _faults(self, now: float) -> Dict[str, float]:
        """Intensités par type de défaut (un défaut principal + effet de l'usure)."""
        eff = self._effective_intensity(now)
        out = {f: 0.0 for f in FAULT_TYPES}
        out[self.fault.fault] = eff
        # L'usure cumulée produit un défaut de roulement précoce naturel
        if self.wear > 0.15:
            out["BEARING_FAULT"] = min(1.0, out["BEARING_FAULT"] + (self.wear - 0.15) * 0.5)
        return out

    # ------------------------------------------------------------------
    #  Génération d'un bloc de signaux
    # ------------------------------------------------------------------
    def generate_block(self, dt: float, now: float) -> Tuple[np.ndarray, np.ndarray]:
        """Retourne (bloc_vibration_g, bloc_courant_A) de longueur BLOCK."""
        f = self._faults(now)
        n = BLOCK
        t = self.uptime_s + np.arange(n) / FS

        # --- Cinématique ---
        target = self.target_rpm if self.running else 0.0
        if f["BELT_SLIP"] > 0:
            target *= (1 - 0.35 * f["BELT_SLIP"])
        if f["OVERLOAD"] > 0:
            target *= (1 - 0.30 * f["OVERLOAD"])
        if self.fault.fault == "MOTOR_STALL":
            target = 0.0
        # Rampe douce + bruit de vitesse (les pas à pas ont un suivi raide mais bruité)
        self._speed_noise = 0.92 * self._speed_noise + 0.08 * self._rng.normal(0, 1)
        target += self._speed_noise * (1.5 if f["BELT_SLIP"] > 0 else 0.35)
        self.rpm += (target - self.rpm) * min(1.0, dt / 0.35)
        if self.running:
            self.uptime_s += dt
            self.running_hours += dt / 3600.0
            # Usure cumulée : l'unité correspond à la durée de vie nominale du moteur
            # (échelle de Weibull du RUL, 6 000 h) — soit environ 0,2 % par heure au
            # régime nominal, accélérée par la charge et par les défauts actifs.
            # Conséquence voulue : une session de démonstration reste nominale (moins
            # de 0,01 point de santé en 3 minutes), alors qu'un mois de service réel
            # produit une dérive mesurable.
            self.wear = min(1.0, self.wear + (dt / (6000.0 * 3600.0))
                            * (1 + 3 * self.load_kg + 8 * sum(f.values())))

        f_rot = max(self.rpm, 0.0) / 60.0                      # fréquence de rotation (Hz)
        # Harmoniques commandées par le driver (pas à pas : micro-stepping)
        f_step = max(f_rot, 0.001) * 200.0                      # résonance de pas

        # --- Vibration (en g) ---
        a1 = 0.045 + 0.55 * f["UNBALANCE"] + 0.06 * self.load_kg          # 1x (déséquilibre)
        a2 = 0.020 + 0.42 * f["MISALIGNMENT"] + 0.25 * f["BELT_SLIP"]      # 2x (désalignement)
        a3 = 0.010 + 0.10 * f["MISALIGNMENT"] + 0.05 * f["OVERLOAD"]

        vib = np.zeros(n, dtype=np.float64)
        vib += a1 * np.sin(2 * np.pi * f_rot * t + 0.3)
        vib += a2 * np.sin(2 * np.pi * 2 * f_rot * t + 1.1)
        vib += a3 * np.sin(2 * np.pi * 3 * f_rot * t + 0.7)
        # Modulation basse fréquence bande/support
        vib += (0.008 + 0.05 * f["BELT_SLIP"]) * np.sin(2 * np.pi * 0.7 * t)

        # Roulement : chocs amortis à la fréquence BPFO, résonance 1,2 kHz porteuse
        kurt_target = 3.0
        if f["BEARING_FAULT"] > 0.01:
            bpfo = max(f_rot * BPFO_MULT, 1.5)
            carrier = np.sin(2 * np.pi * 1200 * t)
            envelope = np.exp(-35 * ((t * bpfo) % 1.0)) * (f["BEARING_FAULT"] ** 0.8)
            vib += 0.35 * envelope * carrier
            kurt_target += 6.0 * f["BEARING_FAULT"]
        # Frottement haute fréquence / résonance de pas
        vib += 0.004 * np.sin(2 * np.pi * f_step * t) * (1 + 0.3 * f["OVERLOAD"])
        # Bruit large bande (proportionnel à la vitesse, comme sur un vrai banc)
        noise_sigma = 0.012 + 0.02 * (self.rpm / max(self.nominal_rpm, 1)) + 0.05 * f["MISALIGNMENT"]
        vib += self._rng.normal(0, noise_sigma, n)
        if self.fault.fault == "SENSOR_FAULT":
            vib[:] = self._sensor_frozen.get("rms", 0.3)
        if not self.running and self.fault.fault != "SENSOR_FAULT":
            vib *= 0.25   # moteur à l'arrêt : vibrations résiduelles uniquement

        # --- Courant (A) ---
        i_base = 0.28 + 0.55 * self.load_kg                       # courant de charge
        i_base += 0.12 * (self.rpm / max(self.nominal_rpm, 1))    # courant de vitesse
        i_base += 0.9 * f["OVERLOAD"] + 0.25 * f["MISALIGNMENT"] + 0.2 * f["BEARING_FAULT"]
        i_base += 0.35 * f["BELT_SLIP"] * 0.4
        cur = np.full(n, i_base, dtype=np.float64)
        cur += 0.05 * np.sin(2 * np.pi * 2 * f_rot * t)
        cur += self._rng.normal(0, 0.03, n)
        if self.fault.fault == "MOTOR_STALL":
            cur *= 1.9    # courant de blocage (moteur qui force sans tourner)
        if not self.running:
            cur *= 0.06   # driver en veille (hold current)

        # --- Buffers glissants pour la FFT ---
        self._buffer = np.roll(self._buffer, -n)
        self._buffer[-n:] = vib.astype(np.float32)
        self._current_buffer = np.roll(self._current_buffer, -n)
        self._current_buffer[-n:] = cur.astype(np.float32)
        return vib, cur

    # ------------------------------------------------------------------
    #  Thermique
    # ------------------------------------------------------------------
    def update_thermal(self, dt: float, current_rms: float, now: float) -> Tuple[float, float]:
        f = self._faults(now)
        # Échauffement calibré sur le banc réel : ~39 °C moteur / ~42 °C roulement
        # à mi-charge (1,2 A nominal), enceinte à 25,7 °C.
        load_heat = 12.0 * (self.load_kg / 1.0) + 10.0 * (current_rms / max(self.nominal_current, 0.6))
        fault_heat = 34.0 * f["OVERHEATING"] + 16.0 * f["OVERLOAD"] + 10.0 * f["BEARING_FAULT"] + 8.0 * f["MISALIGNMENT"]
        target_motor = self.ambient_c + load_heat + fault_heat
        target_bearing = target_motor + 2.2 + 11.0 * f["BEARING_FAULT"] + 3.0 * self.wear + 2.0 * f["BELT_SLIP"]
        if not self.running:
            target_motor = self.ambient_c + 2.0
            target_bearing = self.ambient_c + 2.5
        tau_motor = 55.0 if target_motor > self.temp_motor else 120.0   # refroidissement plus lent
        tau_bearing = 45.0 if target_bearing > self.temp_bearing else 110.0
        self.temp_motor += (target_motor - self.temp_motor) * min(1.0, dt / tau_motor)
        # Le corps moteur chauffe le roulement (couplage thermique)
        self.temp_bearing += (target_bearing - self.temp_bearing) * min(1.0, dt / tau_bearing)
        self.temp_bearing += (self.temp_motor - self.temp_bearing) * min(1.0, dt / 180.0)
        return self.temp_motor, self.temp_bearing

    def settle_thermal(self, current_rms: float, seconds: float = 900.0, dt: float = 10.0,
                       now: float = 0.0) -> None:
        """Amène le modèle thermique à son régime permanent (utilisé pour l'entraînement).

        Évite le biais « moteur froid » : un modèle entraîné sur des échantillons
        froids serait trompé par un banc en régime établi.
        """
        steps = max(1, int(seconds / dt))
        for _ in range(steps):
            self.update_thermal(dt, current_rms, now)

    # ------------------------------------------------------------------
    #  Caractéristiques instantanées à partir des buffers
    # ------------------------------------------------------------------
    def block_stats(self, vib: np.ndarray, cur: np.ndarray) -> Dict[str, float]:
        rms = float(np.sqrt(np.mean(vib ** 2)))
        peak = float(np.max(np.abs(vib)))
        mean = float(np.mean(vib))
        var = float(np.mean((vib - mean) ** 2))
        kurt = float(np.mean((vib - mean) ** 4) / (var ** 2 + 1e-12))
        crest = peak / rms if rms > 1e-9 else 0.0
        return {
            "rms": round(rms, 4),
            "peak": round(peak, 4),
            "kurtosis": round(kurt, 3),
            "crest_factor": round(crest, 3),
            "current_rms": round(float(np.sqrt(np.mean(cur ** 2))), 4),
            "current_peak": round(float(np.max(np.abs(cur))), 4),
        }

    def window_stats(self) -> Dict[str, float]:
        """Statistiques calculées sur la FENÊTRE D'ANALYSE complète (2048 éch. ≈ 2 s).

        C'est ce que fait un vrai nœud capteur : les indicateurs RMS/kurtosis/crête
        sont extraits d'une fenêtre d'analyse, pas d'un bloc de quelques millisecondes
        (sinon les valeurs oscillent d'un facteur 2 selon la phase du cycle).
        """
        vib = self._buffer.astype(np.float64)
        cur = self._current_buffer.astype(np.float64)
        rms_v = float(np.sqrt(np.mean(vib ** 2)))
        peak = float(np.max(np.abs(vib)))
        mean = float(np.mean(vib))
        var = float(np.mean((vib - mean) ** 2))
        kurt = float(np.mean((vib - mean) ** 4) / (var ** 2 + 1e-12))
        return {
            "rms": round(rms_v, 4),
            "peak": round(peak, 4),
            "kurtosis": round(kurt, 3),
            "crest_factor": round(peak / rms_v, 3) if rms_v > 1e-9 else 0.0,
            "current_rms": round(float(np.sqrt(np.mean(cur ** 2))), 4),
            "current_peak": round(float(np.max(np.abs(cur))), 4),
        }

    def spectrum(self) -> Tuple[np.ndarray, np.ndarray]:
        """Spectre d'amplitude du buffer de vibration (fenêtre de Hann)."""
        x = self._buffer.astype(np.float64)
        x = x - x.mean()
        win = np.hanning(len(x))
        spec = np.abs(np.fft.rfft(x * win)) * (2.0 / np.sum(win))
        freqs = np.fft.rfftfreq(len(x), d=1.0 / FS)
        return freqs, spec

    def _amp_at(self, freqs: np.ndarray, spec: np.ndarray, target: float) -> float:
        """Amplitude maximale du spectre dans une fenêtre étroite autour de `target`."""
        window = max(0.6, 0.06 * target)
        mask = (freqs >= target - window) & (freqs <= target + window)
        if not np.any(mask):
            return 0.0
        return float(np.max(spec[mask]))

    def fft_peaks(self, top: int = 6) -> List[Dict[str, float]]:
        """Pics dominants + raies harmoniques 1x/2x/3x de la rotation.

        Les harmoniques sont explicitement échantillonnées : c'est ce que fait
        l'ESP32 (qui connaît la fréquence de rotation commandée) et cela évite les
        erreurs d'appariement de pics que produirait une simple sélection des maxima.
        """
        freqs, spec = self.spectrum()
        mask = (freqs > 0.4) & (freqs < 900)
        f_m, s_m = freqs[mask], spec[mask]
        idx = np.argsort(s_m)[::-1][:top]
        peaks: List[Dict[str, float]] = [
            {"freq": round(float(f_m[i]), 2), "amp": round(float(s_m[i]), 4)} for i in np.sort(idx)
        ]
        f_rot = max(self.rpm, 1.0) / 60.0
        for harmonic in (1, 2, 3):
            target = harmonic * f_rot
            amp = self._amp_at(freqs, spec, target)
            if amp <= 0:
                continue
            # Remplace un pic déjà présent dans la fenêtre, sinon ajoute la raie
            for peak in peaks:
                if abs(peak["freq"] - target) < max(0.6, 0.12 * target):
                    peak["freq"] = round(target, 2)
                    peak["amp"] = round(max(peak["amp"], amp), 4)
                    break
            else:
                peaks.append({"freq": round(target, 2), "amp": round(amp, 4)})
        return sorted(peaks, key=lambda p: p["freq"])

    def band_energy(self, bands: List[Tuple[float, float]]) -> List[float]:
        freqs, spec = self.spectrum()
        out = []
        for lo, hi in bands:
            m = (freqs >= lo) & (freqs < hi)
            out.append(round(float(np.sum(spec[m] ** 2)), 6))
        return out


# Bandes acoustiques par défaut (Hz) : grave / bas-médium / haut-médium / aigu
ACOUSTIC_BANDS = [(20, 200), (200, 800), (800, 2500), (2500, 8000)]


def estimate_spl(vib_rms: float, rpm: float, slip: float, overload: float) -> float:
    """Estimation du niveau sonore (dB SPL) à partir de l'énergie vibratoire."""
    base = 52.0 + 24.0 * min(vib_rms, 3.0) + 0.06 * rpm + 6.0 * slip + 4.0 * overload
    return round(float(np.clip(base, 40, 95)), 1)
