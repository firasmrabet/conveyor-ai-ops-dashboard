"""Service IA : orchestration du moteur de modèles, persistance et diffusion."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from sqlmodel import select

from ..config import settings
from ..db import session_scope
from ..models import FeatureRecord, HealthSnapshot, InferenceRecord, ModelVersion
from ..realtime import hub
from ..schemas import InferencePayload
from .common import logger

# Import du moteur ML (package `ml/` à la racine du backend)
try:
    from ml.features import extract_from_telemetry
    from ml.model import ConveyorAIEngine
except Exception as exc:  # pragma: no cover - dépendances ML absentes
    ConveyorAIEngine = None  # type: ignore
    extract_from_telemetry = None  # type: ignore
    logger.error("Moteur ML indisponible : %s", exc)


class MLService:
    """Encapsule le moteur IA + les buffers de features par moteur."""

    def __init__(self) -> None:
        self.engine: Optional[Any] = None
        self.feature_windows: Dict[str, List[Dict[str, Any]]] = {"M1": [], "M2": []}
        self.rpm_windows: Dict[str, List[float]] = {"M1": [], "M2": []}
        self.last_inference_ts: Dict[str, float] = {"M1": 0.0, "M2": 0.0}
        self.last_snapshot_ts: Dict[str, float] = {"M1": 0.0, "M2": 0.0}
        self.edge_last: Dict[str, Dict[str, Any]] = {}
        self.spectrogram_columns: Dict[str, List[Dict[str, Any]]] = {"M1": [], "M2": []}
        # Lissage temporel des sorties IA (EMA + verrouillage de classe) :
        # évite qu'une classe oscille d'une inférence à l'autre à cause du bruit de mesure.
        self.smoothing: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    def init_engine(self) -> None:
        if ConveyorAIEngine is None:
            logger.warning("Aucun moteur IA chargé — mode dégradé")
            return
        self.engine = ConveyorAIEngine(
            artifacts_dir=settings.ml_artifacts_dir,
            bundle_name=settings.ml_active_bundle,
            default_version=settings.ml_model_version,
        )
        info = self.engine.model_info
        logger.info("Moteur IA actif : %s %s", info["type"], info["version"])
        if info["type"] == "sklearn-bundle":
            meta = info.get("metadata", {})
            logger.info(
                "Artefact chargé (accuracy=%s, f1=%s, RUL MAE=%s h)",
                meta.get("accuracy"), meta.get("f1_macro"), meta.get("rul_mae_hours"),
            )

    def reload(self) -> bool:
        """Recharge l'artefact depuis le disque (hot-swap après entraînement)."""
        if not self.engine:
            self.init_engine()
            return False
        return bool(self.engine.load_artifact())

    # ------------------------------------------------------------------
    def features_from_telemetry(self, motor_id: str, payload: Dict[str, Any]) -> Dict[str, float]:
        """Calcule (ou récupère) le vecteur de features d'une trame de télémétrie."""
        self.rpm_windows.setdefault(motor_id, []).append(float(payload.get("speed", {}).get("rpm", 0.0)))
        if len(self.rpm_windows[motor_id]) > 60:
            del self.rpm_windows[motor_id][:-60]
        if extract_from_telemetry is None:
            return {}
        feats = extract_from_telemetry(payload, None, self.rpm_windows[motor_id])
        # Enrichissement : signature spectrale (peut être fournie par l'edge)
        for peak in payload.get("fft_peaks", [])[:8]:
            feats[f"peak_{round(float(peak.get('freq', 0)), 1)}"] = round(float(peak.get("amp", 0.0)), 5)
        self.feature_windows[motor_id].append({"ts": payload.get("ts", int(time.time() * 1000)), **feats})
        if len(self.feature_windows[motor_id]) > 900:
            del self.feature_windows[motor_id][:-900]
        return feats

    def spectrogram_point(self, motor_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Ajoute une colonne au spectrogramme (temps × fréquence) pour l'UI."""
        peaks = payload.get("fft_peaks") or []
        if not peaks:
            return None
        point = {
            "ts": payload.get("ts", int(time.time() * 1000)),
            "peaks": [{"freq": float(p["freq"]), "amp": float(p["amp"])} for p in peaks[:8]],
            "rms": float(payload.get("vibration", {}).get("rms", 0.0)),
        }
        series = self.spectrogram_columns.setdefault(motor_id, [])
        series.append(point)
        if len(series) > 240:
            del series[: len(series) - 240]
        return point

    # ------------------------------------------------------------------
    async def infer(self, motor_id: str, payload: Dict[str, Any], features: Dict[str, float],
                    source_state: str = "RUNNING") -> Optional[Dict[str, Any]]:
        """Exécute l'inférence (throttlée) + persistance + diffusion WebSocket."""
        if not self.engine:
            return None
        now = time.time()
        if now - self.last_inference_ts.get(motor_id, 0.0) < 1.0 / max(settings.ml_run_inference_hz, 0.1):
            return None
        self.last_inference_ts[motor_id] = now

        # L'inférence est CPU-bornée : on la sort de la boucle asyncio
        wear = float(payload.get("_wear", 0.0))
        result = await asyncio.to_thread(self.engine.predict, motor_id, features,
                                         payload.get("ts", int(now * 1000)), wear)
        result = self._smooth(motor_id, result)
        inference = self._to_payload(motor_id, payload, result)

        # --- Buffers + diffusion ---
        hub.inference[motor_id].push_nowait(inference)
        hub.tick_counters(n_inference=1, n_bytes=len(str(inference)))
        await hub.broadcast({"type": "inference", "motor_id": motor_id, "data": inference}, motors=[motor_id])

        # --- Persistance (features + inférence + snapshot périodique) ---
        await asyncio.to_thread(self._persist, motor_id, payload, features, inference, source_state)
        return inference

    # ------------------------------------------------------------------
    def _smooth(self, motor_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """Lisse les sorties de l'IA pour une lecture « instrument de bord » stable.

        Trois mécanismes :
          1. moyenne mobile exponentielle (EMA) sur la santé, l'anomalie et le RUL ;
          2. EMA sur les probabilités de défaut (le diagnostic devient progressif
             au lieu de sauter d'une classe à l'autre à chaque inférence) ;
          3. verrouillage de classe : un changement de diagnostic doit être confirmé
             deux fois de suite avant d'être publié (le temps que les buffers
             d'analyse se remplissent après un changement de régime).

        Les valeurs brutes restent disponibles dans `raw_*` pour l'audit et l'export.
        """
        alpha = 0.30
        state = self.smoothing.get(motor_id)
        probs = {k: float(v) for k, v in (result.get("fault_probabilities") or {}).items()}

        if state is None:
            state = {
                "health": float(result["health_index"]),
                "anomaly": float(result["anomaly_score"]),
                "rul": float(result["rul_hours"]),
                "probs": probs,
                "class": str(result["fault_class"]),
                "candidate": None,
                "strikes": 0,
            }
            self.smoothing[motor_id] = state
            result["smoothed"] = False
            return result

        state["health"] = (1 - alpha) * state["health"] + alpha * float(result["health_index"])
        state["anomaly"] = (1 - alpha) * state["anomaly"] + alpha * float(result["anomaly_score"])
        state["rul"] = (1 - alpha) * state["rul"] + alpha * float(result["rul_hours"])
        merged = {**state["probs"]}
        for key in set(probs) | set(merged):
            merged[key] = (1 - alpha) * float(merged.get(key, 0.0)) + alpha * float(probs.get(key, 0.0))
        state["probs"] = merged

        # Verrouillage de classe : 2 confirmations consécutives avant de changer d'avis
        top = max(merged.items(), key=lambda kv: kv[1])[0] if merged else state["class"]
        if top != state["class"]:
            state["strikes"] = state["strikes"] + 1 if state["candidate"] == top else 1
            state["candidate"] = top
            if state["strikes"] >= 2:
                state["class"] = top
                state["candidate"] = None
                state["strikes"] = 0
        else:
            state["candidate"] = None
            state["strikes"] = 0

        result["raw_health_index"] = result["health_index"]
        result["raw_anomaly_score"] = result["anomaly_score"]
        result["raw_fault_class"] = result["fault_class"]
        result["raw_rul_hours"] = result["rul_hours"]
        result["health_index"] = round(state["health"], 2)
        result["anomaly_score"] = round(min(1.0, max(0.0, state["anomaly"])), 4)
        result["is_anomaly"] = bool(result["anomaly_score"] > 0.6)
        result["fault_class"] = state["class"]
        result["fault_probabilities"] = {k: round(v, 4) for k, v in
                                         sorted(merged.items(), key=lambda kv: -kv[1])}
        result["rul_hours"] = round(max(0.5, state["rul"]), 1)
        spread = max(0.18, 1.0 - result["health_index"] / 100.0)
        low, high = result.get("rul_confidence", {"low": 0.0, "high": 0.0}).get("low", 0.0), \
            result.get("rul_confidence", {"low": 0.0, "high": 0.0}).get("high", 0.0)
        width = max(0.25 * result["rul_hours"], (high - low) / 2.0 if high > low else 0.3 * result["rul_hours"])
        result["rul_confidence"] = {"low": round(max(0.5, result["rul_hours"] - width), 1),
                                    "high": round(result["rul_hours"] + width * (0.6 + spread), 1)}
        result["smoothed"] = True
        return result

    def _to_payload(self, motor_id: str, telemetry: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise la sortie du modèle au format contractuel du cahier des charges."""
        try:
            model = InferencePayload(
                motor_id=motor_id,
                ts=int(telemetry.get("ts", time.time() * 1000)),
                health_index=float(result["health_index"]),
                anomaly_score=float(result["anomaly_score"]),
                is_anomaly=bool(result["is_anomaly"]),
                fault_class=str(result["fault_class"]),
                fault_probabilities={k: float(v) for k, v in result["fault_probabilities"].items()},
                rul_hours=float(result["rul_hours"]),
                rul_confidence={k: float(v) for k, v in result["rul_confidence"].items()},
                explanation=result["explanation"],
                model_version=str(result["model_version"]),
                inference_ms=float(result["inference_ms"]),
            )
            return model.model_dump()
        except Exception as exc:  # pragma: no cover
            logger.error("Sortie modèle non conforme : %s", exc)
            return {
                "motor_id": motor_id, "ts": telemetry.get("ts", int(time.time() * 1000)),
                "health_index": result.get("health_index", 100.0),
                "anomaly_score": result.get("anomaly_score", 0.0),
                "is_anomaly": result.get("is_anomaly", False),
                "fault_class": result.get("fault_class", "HEALTHY"),
                "fault_probabilities": result.get("fault_probabilities", {}),
                "rul_hours": result.get("rul_hours", 0.0),
                "rul_confidence": result.get("rul_confidence", {"low": 0, "high": 0}),
                "explanation": result.get("explanation", []),
                "model_version": result.get("model_version", "v0"),
                "inference_ms": result.get("inference_ms", 0.0),
            }

    def _persist(self, motor_id: str, telemetry: Dict[str, Any], features: Dict[str, float],
                 inference: Dict[str, Any], state: str) -> None:
        ts = int(inference.get("ts", time.time() * 1000))
        with session_scope() as session:
            session.add(FeatureRecord(ts=ts, motor_id=motor_id, features=features,
                                      spectral={"peaks": telemetry.get("fft_peaks", [])[:8]}, state=state))
            session.add(InferenceRecord(
                ts=ts, motor_id=motor_id, source="server",
                health_index=inference["health_index"], anomaly_score=inference["anomaly_score"],
                is_anomaly=inference["is_anomaly"], fault_class=inference["fault_class"],
                fault_probabilities=inference["fault_probabilities"],
                rul_hours=inference["rul_hours"], rul_low=inference["rul_confidence"]["low"],
                rul_high=inference["rul_confidence"]["high"], explanation=inference["explanation"],
                model_version=inference["model_version"], inference_ms=inference["inference_ms"],
            ))
            # Snapshot santé toutes les 5 s (agrégats, heatmap, MTBF)
            if time.time() - self.last_snapshot_ts.get(motor_id, 0.0) >= 5.0:
                self.last_snapshot_ts[motor_id] = time.time()
                temp = telemetry.get("temperature", {})
                session.add(HealthSnapshot(
                    ts=ts, motor_id=motor_id, health_index=inference["health_index"],
                    anomaly_score=inference["anomaly_score"], state=state,
                    fault_class=inference["fault_class"],
                    vibration_rms=float(telemetry.get("vibration", {}).get("rms", 0.0)),
                    current_rms=float(telemetry.get("current", {}).get("rms_a", 0.0)),
                    temp_bearing_c=float(temp.get("bearing_c", 0.0)),
                    rpm=float(telemetry.get("speed", {}).get("rpm", 0.0)),
                ))

    # ------------------------------------------------------------------
    def register_edge_inference(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enregistre une inférence edge (TinyML) et compare au modèle serveur."""
        motor_id = payload.get("motor_id", "M1")
        hub.edge_inference[motor_id].push_nowait(payload)
        self.edge_last[motor_id] = payload
        server = hub.inference[motor_id].last()
        comparison = hub.edge_comparison.setdefault(
            motor_id, {"agreement": 1.0, "samples": 0, "edge_ms": 3.0, "server_ms": 8.0, "mismatch": 0})
        if server:
            comparison["samples"] += 1
            same = payload.get("fault_class") == server.get("fault_class")
            if not same:
                comparison["mismatch"] += 1
            n = comparison["samples"]
            comparison["agreement"] = round(1.0 - comparison["mismatch"] / max(n, 1), 4)
            comparison["edge_ms"] = round(float(payload.get("inference_ms", comparison["edge_ms"])), 2)
            comparison["server_ms"] = round(float(server.get("inference_ms", comparison["server_ms"])), 2)
        return comparison

    # ------------------------------------------------------------------
    def current_inference(self, motor_id: str) -> Optional[Dict[str, Any]]:
        return hub.inference[motor_id].last()

    def degradation_curve(self, motor_id: str, hours: float = 168.0) -> Dict[str, Any]:
        if not self.engine:
            return {"history": [], "projection": [], "per_day": 0.0}
        hist = self.engine.degradation(motor_id)
        projection = self.engine.project_health(motor_id, hours=hours)
        return {"history": hist["history"], "projection": projection, "per_day": hist["per_day"]}

    def radar(self, motor_id: str) -> Dict[str, Any]:
        if not self.engine:
            return {"baseline": [], "current": []}
        return self.engine.radar_signature(motor_id)

    def spectrogram(self, motor_id: str) -> List[Dict[str, Any]]:
        """Colonnes du spectrogramme (temps × fréquence) pour l'UI."""
        return self.spectrogram_columns.get(motor_id, [])

    def record_model_version(self, metrics: Dict[str, Any]) -> None:
        """Enregistre une version de modèle (utilisé par /api/models/sync)."""
        with session_scope() as session:
            existing = session.exec(
                select(ModelVersion).where(ModelVersion.version == metrics.get("version", "v0"))
            ).first()
            if existing:
                return
            session.add(ModelVersion(
                name="conveyor-anomaly", version=str(metrics.get("version", "v0")), kind="server",
                size_kb=float(metrics.get("artifact_kb", 0.0)),
                latency_ms=float(metrics.get("inference_ms", 0.0)) or 8.0,
                accuracy=float(metrics.get("accuracy", 0.0)), f1=float(metrics.get("f1_macro", 0.0)),
                precision=float(metrics.get("precision_macro", 0.0)),
                recall=float(metrics.get("recall_macro", 0.0)),
                roc_auc=float(metrics.get("roc_auc") or 0.0),
                rul_mae=float(metrics.get("rul_mae_hours", 0.0)),
                active=bool(self.engine and self.engine.bundle is not None),
                metrics=metrics,
            ))


ml_service = MLService()
