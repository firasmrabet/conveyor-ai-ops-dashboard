"""Modèle IA de supervision : anomalie, classification de défaut, RUL, XAI.

Deux implémentations interchangeables derrière la même interface :

1. `HeuristicModel` — modèle « physique informé » : toujours disponible, sans
   dépendance, il sert de repli et de modèle par défaut au premier lancement.
2. `SklearnBundle`  — artefact réel entraîné par `ml/train.py`
   (IsolationForest + RandomForest + Ridge) chargé depuis `artifacts/*.joblib`.

`ConveyorAIEngine` choisit automatiquement le meilleur modèle disponible :
il suffit de déposer vos propres artefacts pour qu'ils prennent la main,
sans modifier le reste du code (cf. README § « Brancher vos modèles »).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .features import BASELINE, FEATURE_NAMES, TOLERANCE, deviation

# Porte d'anomalie : en dessous de ce score, aucun défaut n'est nommé (cf. SklearnBundle.predict).
ANOMALY_GATE: float = 0.32
# Températures (°C) au-delà desquelles une surchauffe est diagnostiquée même si le score
# d'anomalie statistique reste modéré (mesure directe > inférence).
THERMAL_OVERRIDE_C: float = 55.0
THERMAL_DELTA_OVERRIDE_C: float = 30.0

FAULT_CLASSES: List[str] = [
    "HEALTHY",
    "BEARING_FAULT",
    "UNBALANCE",
    "MISALIGNMENT",
    "BELT_SLIP",
    "OVERLOAD",
    "OVERHEATING",
]

# Libellés FR pour les explications XAI
FEATURE_LABELS: Dict[str, str] = {
    "vibration_rms": "RMS vibratoire",
    "vibration_peak": "pic vibratoire",
    "kurtosis": "kurtosis (chocs)",
    "crest_factor": "facteur de crête",
    "h1_ratio": "harmonique 1x (déséquilibre)",
    "h2_ratio": "harmonique 2x (désalignement)",
    "hf_ratio": "énergie hautes fréquences",
    "band_hf_energy": "énergie de bande HF",
    "current_rms": "courant RMS",
    "current_peak": "courant crête",
    "power_w": "puissance absorbée",
    "current_ripple": "ondulation de courant",
    "motor_temp": "température moteur",
    "bearing_temp": "température roulement",
    "temp_delta": "élévation thermique roulement/ambiante",
    "rpm": "vitesse de rotation",
    "speed_error": "erreur de vitesse",
    "rpm_variance": "instabilité de vitesse",
    "belt_speed_error": "écart vitesse de bande",
    "spl_db": "niveau sonore",
    "load_estimate": "charge estimée",
}

# Poids des signatures par défaut (physique de la machine) -------------------
# Chaque défaut est décrit par une combinaison pondérée d'écarts normalisés.
FAULT_SIGNATURES: Dict[str, Dict[str, float]] = {
    "BEARING_FAULT": {
        "kurtosis": 0.85, "crest_factor": 0.7, "band_hf_energy": 0.9, "hf_ratio": 0.75,
        "bearing_temp": 0.8, "vibration_rms": 0.5, "bearing_temp": 0.8, "temp_delta": 0.5, "spl_db": 0.35,
    },
    "UNBALANCE": {
        "h1_ratio": 1.0, "vibration_rms": 0.7, "vibration_peak": 0.5, "spl_db": 0.4, "current_ripple": 0.3,
    },
    "MISALIGNMENT": {
        "h2_ratio": 1.0, "vibration_rms": 0.6, "current_rms": 0.4, "motor_temp": 0.4, "spl_db": 0.4,
    },
    "BELT_SLIP": {
        "belt_speed_error": 1.1, "speed_error": 0.8, "rpm_variance": 0.7, "spl_db": 0.5,
        "current_ripple": 0.4, "temp_delta": 0.3,
    },
    "OVERLOAD": {
        "current_rms": 1.0, "power_w": 0.9, "current_peak": 0.6, "motor_temp": 0.6,
        "speed_error": 0.4, "load_estimate": 0.8, "spl_db": 0.3,
    },
    "OVERHEATING": {
        "motor_temp": 1.0, "bearing_temp": 0.9, "temp_delta": 0.7, "power_w": 0.2,
    },
}

# Seuils de sécurité par défaut : type d'alerte -> (métrique, opérateur, seuil, sévérité)
DEFAULT_RULES: List[Dict[str, Any]] = [
    {"name": "Vibration RMS élevée", "metric": "vibration_rms", "operator": ">", "threshold": 0.35,
     "hysteresis": 0.04, "duration_s": 20, "severity": "WARNING", "description": "Usure/déséquilibre naissant"},
    {"name": "Vibration RMS critique", "metric": "vibration_rms", "operator": ">", "threshold": 0.55,
     "hysteresis": 0.05, "duration_s": 5, "severity": "CRITICAL", "description": "Défaut mécanique avéré"},
    {"name": "Température roulement", "metric": "bearing_temp", "operator": ">", "threshold": 65.0,
     "hysteresis": 2.0, "duration_s": 15, "severity": "WARNING", "description": "Surchauffe roulement"},
    {"name": "Surchauffe moteur", "metric": "motor_temp", "operator": ">", "threshold": 75.0,
     "hysteresis": 3.0, "duration_s": 10, "severity": "CRITICAL", "description": "Risque de déclassement aimants"},
    {"name": "Courant excessif", "metric": "current_rms", "operator": ">", "threshold": 1.5,
     "hysteresis": 0.1, "duration_s": 8, "severity": "WARNING", "description": "Charge anormale / frottement"},
    {"name": "Santé moteur basse", "metric": "health_index", "operator": "<", "threshold": 55.0,
     "hysteresis": 3.0, "duration_s": 30, "severity": "WARNING", "description": "Dégradation avancée"},
    {"name": "Santé moteur critique", "metric": "health_index", "operator": "<", "threshold": 30.0,
     "hysteresis": 2.0, "duration_s": 10, "severity": "CRITICAL", "description": "Arrêt de maintenance requis"},
    {"name": "Chute de vitesse", "metric": "rpm", "operator": "<", "threshold": 90.0,
     "hysteresis": 4.0, "duration_s": 10, "severity": "WARNING", "description": "Glissement de bande ou blocage"},
    {"name": "Anomalie IA", "metric": "anomaly_score", "operator": ">", "threshold": 0.65,
     "hysteresis": 0.05, "duration_s": 5, "severity": "WARNING", "description": "Score d'anomalie de l'Isolation Forest"},
]


def softmax(values: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    v = np.asarray(values, dtype=np.float64) / max(temperature, 1e-6)
    v = v - v.max()
    e = np.exp(v)
    return e / (e.sum() + 1e-12)


# ==========================================================================
#  1. Modèle heuristique (physique informé) — toujours disponible
# ==========================================================================
class HeuristicModel:
    """Diagnostic par signatures : robuste, explicable, sans données d'entraînement."""

    name = "heuristic-physics-informed"
    version = "v1.4.0"

    def anomaly_score(self, devs: Dict[str, float]) -> float:
        """Score d'anomalie ∈ [0,1] : fusion non linéaire des écarts normalisés.

        On utilise une moyenne quadratique pondérée : un seul écart important
        suffit à faire monter le score (comportement attendu en surveillance).
        """
        weights = {
            "vibration_rms": 1.0, "kurtosis": 0.7, "crest_factor": 0.5, "hf_ratio": 0.6,
            "band_hf_energy": 0.8, "current_rms": 0.8, "current_peak": 0.5, "power_w": 0.5,
            "motor_temp": 0.7, "bearing_temp": 0.8, "temp_delta": 0.5, "speed_error": 0.6,
            "rpm_variance": 0.6, "belt_speed_error": 0.8, "spl_db": 0.4, "h1_ratio": 0.6, "h2_ratio": 0.6,
        }
        acc = 0.0
        wsum = 0.0
        for name, w in weights.items():
            d = max(0.0, devs.get(name, 0.0))
            acc += w * (d / 2.5) ** 2
            wsum += w
        mse = acc / max(wsum, 1e-9)
        # Réponse graduée : ~0,15 au repos, ~0,5 à la limite de tolérance, >0,85 en défaut franc
        return float(np.clip(1.0 - np.exp(-1.35 * np.sqrt(mse)), 0.0, 1.0))

    def fault_probabilities(self, devs: Dict[str, float]) -> Dict[str, float]:
        scores = {"HEALTHY": 0.9}
        for fault, signature in FAULT_SIGNATURES.items():
            s = 0.0
            for feat, weight in signature.items():
                s += weight * max(0.0, devs.get(feat, 0.0))
            scores[fault] = s
        keys = list(scores.keys())
        raw = np.array([scores[k] for k in keys])
        # Température basse => distribution tranchée dès qu'un défaut domine
        probs = softmax(raw, temperature=0.55)
        return {k: round(float(p), 4) for k, p in zip(keys, probs)}

    @staticmethod
    def fuse_health(anomaly: float, worst_fault: float, bearing_temp: float = 42.0,
                    wear: float = 0.0) -> float:
        """Fusion en Health Index 0–100.

        Zone morte volontaire : en dessous du seuil d'anomalie (0,28), la machine est
        considérée saine (santé ≈ 90–100) — évite les faux positifs permanents.
        """
        anomaly_penalty = 62.0 * max(0.0, anomaly - 0.28) / 0.72
        thermal_penalty = 12.0 * max(0.0, (bearing_temp - 58.0) / 22.0)
        health = 100.0 - anomaly_penalty - 20.0 * worst_fault - thermal_penalty - 8.0 * wear
        return float(np.clip(health, 0.0, 100.0))

    def health_index(self, anomaly: float, probs: Dict[str, float], feats: Dict[str, float],
                     wear: float = 0.0) -> float:
        worst = max((p for k, p in probs.items() if k != "HEALTHY"), default=0.0)
        return self.fuse_health(anomaly, worst, feats.get("bearing_temp", 42.0), wear)

    def rul(self, health: float, health_history: List[Tuple[int, float]],
            rpm: float, running: bool) -> Tuple[float, float, float]:
        """Estimation du RUL (heures) + bande de confiance — modèle de dégradation type Weibull.

        RUL de base : décroissance exponentielle de la durée de vie restante avec la perte
        de santé, adoucie par un régime quadratique sous le seuil critique (40/100).
        La pente de dégradation observée (points de santé/heure) affine ensuite le résultat :
        un moteur qui chute vite consomme son capital plus vite qu'un moteur en dérive lente.
        """
        LIFETIME_H = 6000.0     # durée de vie nominale d'un roulement (heures de service)
        CRITICAL = 40.0         # seuil de bascule vers le régime dégradé
        health = float(np.clip(health, 0.0, 100.0))

        if health >= CRITICAL:
            rul = LIFETIME_H * math.exp(-(100.0 - health) / 22.0)
        else:
            base = LIFETIME_H * math.exp(-(100.0 - CRITICAL) / 22.0)   # ≈ 408 h à 40/100
            rul = base * (health / CRITICAL) ** 2.5

        # Vitesse de dégradation observée : on compare la santé récente à la santé
        # initiale de la fenêtre (médianes = insensibles au bruit d'inférence, contrairement
        # à une régression qui réagissait à quelques dixièmes de point de bruit et faisait
        # sauter le RUL d'un facteur 3 en régime nominal).
        if len(health_history) >= 20:
            ts = np.array([t for t, _ in health_history], dtype=np.float64) / 3.6e6   # heures
            hs = np.array([h for _, h in health_history], dtype=np.float64)
            span_h = float(ts[-1] - ts[0])
            if span_h > 0.005:                          # ≥ ~18 s d'historique exploitable
                window = max(5, len(hs) // 3)
                drop = float(np.median(hs[:window]) - np.median(hs[-window:]))
                rate = drop / max(span_h, 1e-3)         # points de santé perdus par heure
                if drop >= 3.0 and rate > 20.0:         # dégradation franche et continue
                    rul *= float(np.clip(1.0 - 0.0015 * rate, 0.45, 1.0))

        if not running:
            rul *= 1.15              # à l'arrêt, l'usure ralentit
        if rpm > 140:
            rul *= 0.9
        rul = float(np.clip(rul, 0.5, LIFETIME_H))
        spread = float(np.clip(0.18 + 0.35 * (1 - health / 100.0), 0.15, 0.55))
        return round(rul, 1), round(max(0.1, rul * (1 - spread)), 1), round(rul * (1 + spread), 1)

    def explain(self, devs: Dict[str, float], probs: Dict[str, float], top: int = 6) -> List[Dict[str, Any]]:
        """Contribution de chaque feature au diagnostic (barres XAI)."""
        contributions: List[Tuple[str, float]] = []
        for name, dev in devs.items():
            if name not in FEATURE_LABELS:
                continue
            weight = 1.0
            for sig in FAULT_SIGNATURES.values():
                weight = max(weight, sig.get(name, 0.0))
            score = abs(dev) * weight
            if score > 0.02:
                contributions.append((name, score))
        contributions.sort(key=lambda kv: -kv[1])
        total = sum(v for _, v in contributions) or 1.0
        out = [
            {"feature": name, "label": FEATURE_LABELS.get(name, name), "contribution": round(v / total, 4),
             "deviation": round(devs.get(name, 0.0), 3)}
            for name, v in contributions[:top]
        ]
        return out

    def predict(self, feats: Dict[str, float], health_history: List[Tuple[int, float]],
                wear: float = 0.0) -> Dict[str, Any]:
        started = time.perf_counter()
        devs = {name: deviation(feats, name) for name in FEATURE_NAMES}
        anomaly = self.anomaly_score(devs)
        probs = self.fault_probabilities(devs)
        fault_class = max(probs.items(), key=lambda kv: kv[1])[0]
        health = self.health_index(anomaly, probs, feats, wear)
        rul, rul_low, rul_high = self.rul(health, health_history, feats.get("rpm", 0.0),
                                          feats.get("rpm", 0.0) > 5)
        return {
            "health_index": round(health, 1),
            "anomaly_score": round(anomaly, 4),
            "is_anomaly": bool(anomaly > 0.6),
            "fault_class": fault_class,
            "fault_probabilities": probs,
            "rul_hours": rul,
            "rul_confidence": {"low": rul_low, "high": rul_high},
            "explanation": self.explain(devs, probs),
            "model_version": self.version,
            "inference_ms": round((time.perf_counter() - started) * 1000, 2),
        }


# ==========================================================================
#  2. Artefact scikit-learn (IsolationForest + RandomForest + Ridge)
# ==========================================================================
class SklearnBundle:
    """Charge un artefact `.joblib` produit par `ml/train.py` (ou vos modèles)."""

    def __init__(self, path: Path, version: str = "v1.4.0") -> None:
        import joblib  # import tardif : le bundle est optionnel

        bundle = joblib.load(path)
        self.scaler = bundle["scaler"]
        self.iso = bundle["isolation_forest"]
        self.clf = bundle["classifier"]
        self.rul = bundle["rul_model"]
        self.feature_names: List[str] = bundle.get("feature_names", FEATURE_NAMES)
        self.version = bundle.get("version", version)
        self.metadata: Dict[str, Any] = bundle.get("metadata", {})
        self.path = path
        # Étalonnage du score d'anomalie : moyenne/écart sur la distribution saine
        self.iso_offset = float(bundle.get("iso_offset", 0.0))
        self.iso_scale = float(bundle.get("iso_scale", 1.0)) or 1.0

    @staticmethod
    def as_matrix(feats: Dict[str, float], names: List[str]) -> np.ndarray:
        return np.array([[float(feats.get(n, BASELINE.get(n, 0.0))) for n in names]], dtype=np.float64)

    def predict(self, feats: Dict[str, float], health_history: List[Tuple[int, float]],
                wear: float = 0.0) -> Dict[str, Any]:
        started = time.perf_counter()
        x = self.scaler.transform(self.as_matrix(feats, self.feature_names))

        # --- Anomalie ---
        raw = float(-self.iso.score_samples(x)[0])
        # Étalonnage empirique (cf. ml/train.py) : le jeu sain se situe à z ≈ 0,4 et les
        # défauts francs à z ≈ 4,2 → échelle 4,2 avec un léger plancher de bruit.
        z_score = (raw - self.iso_offset) / (self.iso_scale or 1.0)
        # Carte anomalie : z = 0 → régime de référence, z = 1 → valeur typique d'un
        # défaut franc (≈ 6 σ sur la distribution saine). Au-delà de 0,6 le
        # système considère le comportement comme anormal (cf. is_anomaly).
        anomaly = float(np.clip(z_score / 6.0 + 0.05, 0.0, 1.0))

        # --- Classification de défaut ---
        proba = self.clf.predict_proba(x)[0]
        classes = list(getattr(self.clf, "classes_", FAULT_CLASSES))
        probs = {str(c): round(float(p), 4) for c, p in zip(classes, proba)}
        for fc in FAULT_CLASSES:
            probs.setdefault(fc, 0.0)
        # Marge de décision : une classe de défaut n'est annoncée qu'avec une probabilité
        # franche (≥ 0,45). En dessous, le diagnostic reste « sain / incertain », ce qui
        # supprime l'oscillation observée à la frontière de décision.
        best_fault = max(((k, v) for k, v in probs.items() if k != "HEALTHY"), key=lambda kv: kv[1])
        fault_class = best_fault[0] if best_fault[1] >= 0.45 else "HEALTHY"
        probs = {k: (round(v, 4) if k != "HEALTHY" else round(1.0 - best_fault[1], 4)
                     if fault_class == "HEALTHY" else v) for k, v in probs.items()}

        # --- Garde-fou « anomalie d'abord » -----------------------------------------
        # L'Isolation Forest (non supervisé) sert de porte au classifieur supervisé :
        # si le comportement du banc est statistiquement normal, aucune classe de défaut
        # n'est annoncée — même si le classifieur a une préférence marginale pour l'une
        # d'elles. C'est la stratégie d'un vrai système de maintenance prédictive
        # (détecter puis nommer), et cela supprime les faux positifs en régime nominal.
        if anomaly < ANOMALY_GATE:
            residual = {k: v * 0.35 for k, v in probs.items() if k != "HEALTHY"}
            healthy_p = max(0.55, 1.0 - sum(residual.values()))
            total = sum(residual.values()) + healthy_p or 1.0
            probs = {k: round(v / total, 4) for k, v in residual.items()}
            probs["HEALTHY"] = round(healthy_p / total, 4)
            fault_class = "HEALTHY"

            # --- Garde-fou thermique absolu ------------------------------------
            # Une surchauffe est une GRANDEUR MESURÉE, pas une inférence : elle monte
            # lentement et peut rester sous le seuil statistique de l'Isolation Forest.
            # Si les températures dépassent les limites constructeur alors que la
            # détection d'anomalie est muette, le diagnostic reste OVERHEATING.
            if (feats.get("bearing_temp", 0.0) >= THERMAL_OVERRIDE_C
                    or feats.get("motor_temp", 0.0) >= THERMAL_OVERRIDE_C
                    or feats.get("temp_delta", 0.0) >= THERMAL_DELTA_OVERRIDE_C):
                fault_class = "OVERHEATING"
                boosted = {k: v * 0.25 for k, v in probs.items()}
                boosted["HEALTHY"] = 0.25
                boosted["OVERHEATING"] = 0.72
                total = sum(boosted.values()) or 1.0
                probs = {k: round(v / total, 4) for k, v in boosted.items()}

        # --- Health index (fusion) ---
        worst = max((p for k, p in probs.items() if k != "HEALTHY"), default=0.0)
        health = HeuristicModel.fuse_health(anomaly, worst, feats.get("bearing_temp", 42.0), wear)

        # --- RUL : base physique (Weibull) ajustée par le régresseur ML, facteur borné ---
        rul_phys, _, _ = HeuristicModel().rul(health, health_history,
                                              feats.get("rpm", 0.0), feats.get("rpm", 0.0) > 5)
        rul_reg = float(self.rul.predict(x)[0])
        correction = float(np.clip(rul_reg / max(rul_phys, 1.0), 0.6, 1.6))
        rul_pred = float(np.clip(rul_phys * correction, 0.5, 6000.0))
        spread = float(np.clip(0.18 + 0.35 * (1 - health / 100.0), 0.15, 0.55))

        # --- XAI : importance globale × écart normalisé (approximation SHAP locale) ---
        importances = getattr(self.clf, "feature_importances_", None)
        devs = {n: deviation(feats, n) for n in self.feature_names}
        contribs: List[Tuple[str, float, float]] = []
        if importances is not None:
            for i, name in enumerate(self.feature_names):
                score = float(importances[i]) * abs(devs.get(name, 0.0))
                if score > 1e-4:
                    contribs.append((name, score, devs.get(name, 0.0)))
        contribs.sort(key=lambda t: -t[1])
        total = sum(c[1] for c in contribs) or 1.0
        explanation = [
            {"feature": n, "label": FEATURE_LABELS.get(n, n),
             "contribution": round(v / total, 4), "deviation": round(d, 3)}
            for n, v, d in contribs[:6]
        ]

        return {
            "health_index": round(health, 1),
            "anomaly_score": round(anomaly, 4),
            "is_anomaly": bool(anomaly > 0.6 or (self.iso.predict(x)[0] == -1 and anomaly > 0.45)),
            "fault_class": fault_class,
            "fault_probabilities": probs,
            "rul_hours": round(rul_pred, 1),
            "rul_confidence": {"low": round(max(0.1, rul_pred * (1 - spread)), 1),
                               "high": round(rul_pred * (1 + spread), 1)},
            "explanation": explanation,
            "model_version": self.version,
            "inference_ms": round((time.perf_counter() - started) * 1000, 2),
        }


# ==========================================================================
#  3. Moteur unifié
# ==========================================================================
@dataclass
class MotorState:
    """État courant d'un moteur vu par l'IA."""

    motor_id: str
    health_history: List[Tuple[int, float]] = field(default_factory=list)
    last_features: Dict[str, float] = field(default_factory=dict)
    last_inference: Dict[str, Any] = field(default_factory=dict)
    wear: float = 0.0
    cached_rul: float = 0.0
    last_rul_ts: float = 0.0

    def push_health(self, ts: int, health: float, keep: int = 4000) -> None:
        self.health_history.append((ts, health))
        if len(self.health_history) > keep:
            del self.health_history[: len(self.health_history) - keep]

    def degradation_per_day(self) -> float:
        """Pente de dégradation (points de santé / jour) sur l'historique récent."""
        hist = self.health_history[-600:]
        if len(hist) < 5:
            return 0.0
        ts = np.array([t for t, _ in hist], dtype=np.float64) / 3.6e6  # heures
        hs = np.array([h for _, h in hist], dtype=np.float64)
        if ts[-1] - ts[0] < 1e-5:
            return 0.0
        return float(np.polyfit(ts, hs, 1)[0] * 24.0)


class ConveyorAIEngine:
    """Point d'entrée unique utilisé par l'API : choisit et applique le modèle."""

    def __init__(self, artifacts_dir: str | Path = "./artifacts", bundle_name: str = "conveyor-synth",
                 default_version: str = "v1.4.0") -> None:
        self.artifacts_dir = Path(artifacts_dir)
        self.bundle_name = bundle_name
        self.heuristic = HeuristicModel()
        self.heuristic.version = default_version
        self.bundle: Optional[SklearnBundle] = None
        self.motors: Dict[str, MotorState] = {"M1": MotorState("M1"), "M2": MotorState("M2")}
        self.loaded_at: float = 0.0
        self.load_artifact()

    # ------------------------------------------------------------------
    def load_artifact(self, path: Optional[Path] = None) -> bool:
        """Charge un artefact joblib s'il existe. Retourne True si chargé."""
        candidate = path or (self.artifacts_dir / f"{self.bundle_name}.joblib")
        if candidate.exists():
            try:
                self.bundle = SklearnBundle(candidate)
                self.loaded_at = time.time()
                return True
            except Exception:  # artefact corrompu ou dépendance manquante
                self.bundle = None
        return False

    @property
    def active_model(self) -> Any:
        return self.bundle or self.heuristic

    @property
    def model_info(self) -> Dict[str, Any]:
        active = self.active_model
        return {
            "name": getattr(active, "name", "sklearn-bundle") if not self.bundle else "sklearn-bundle",
            "version": active.version,
            "type": "sklearn-bundle" if self.bundle else "heuristic-physics-informed",
            "path": str(getattr(self.bundle, "path", "")) if self.bundle else "",
            "metadata": getattr(self.bundle, "metadata", {}) if self.bundle else {},
        }

    # ------------------------------------------------------------------
    def predict(self, motor_id: str, feats: Dict[str, float], ts: Optional[int] = None,
                wear: float = 0.0) -> Dict[str, Any]:
        state = self.motors.setdefault(motor_id, MotorState(motor_id))
        state.last_features = dict(feats)
        state.wear = wear
        result = self.active_model.predict(feats, state.health_history, wear)
        state.last_inference = result
        state.push_health(ts or int(time.time() * 1000), result["health_index"])
        return result

    def degradation(self, motor_id: str) -> Dict[str, Any]:
        state = self.motors.get(motor_id)
        if not state:
            return {"per_day": 0.0, "history": [], "projection": []}
        return {
            "per_day": round(state.degradation_per_day(), 3),
            "history": [{"ts": t, "health": h} for t, h in state.health_history[-720:]],
        }

    def project_health(self, motor_id: str, hours: float = 168.0, points: int = 48) -> List[Dict[str, float]]:
        """Projection de la santé future (utilisée par les courbes en pointillés)."""
        state = self.motors.get(motor_id)
        if not state or not state.health_history:
            return []
        current = state.health_history[-1][1]
        per_hour = state.degradation_per_day() / 24.0 or -0.05
        now_ms = state.health_history[-1][0]
        out = []
        for i in range(points + 1):
            h = hours * i / points
            health = float(np.clip(current + per_hour * h, 0.0, 100.0))
            out.append({"ts": now_ms + h * 3.6e6, "health": round(health, 1), "hours": round(h, 1)})
        return out

    def radar_signature(self, motor_id: str) -> Dict[str, List[Dict[str, float]]]:
        """Signature actuelle vs baseline saine (radar chart)."""
        state = self.motors.get(motor_id)
        feats = state.last_features if state else {}
        axes = ["vibration_rms", "kurtosis", "h1_ratio", "h2_ratio", "current_rms",
                "motor_temp", "bearing_temp", "belt_speed_error", "spl_db"]

        def normalize(name: str, value: float) -> float:
            base, tol = BASELINE.get(name, 1.0), TOLERANCE.get(name, 1.0)
            if name in ("motor_temp", "bearing_temp"):
                # Températures : échelle 25 → 90 °C
                return float(np.clip((value - 25.0) / 65.0 * 100, 0, 100))
            return float(np.clip(100 * (value / (base + tol)) / 2.0, 0, 100))

        baseline_series = [{"axis": n, "value": round(normalize(n, BASELINE.get(n, 0.0)), 1)} for n in axes]
        current_series = [{"axis": n, "value": round(normalize(n, feats.get(n, BASELINE.get(n, 0.0))), 1)}
                          for n in axes]
        return {"baseline": baseline_series, "current": current_series}
