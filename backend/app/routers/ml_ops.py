"""API MLOps : versions de modèle, métriques, drift, comparaison edge/serveur, rollback."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, desc, select

from ..config import settings
from ..db import get_session
from ..models import InferenceRecord, ModelVersion, now_ts_ms
from ..realtime import hub
from ..services.common import logger
from ..services.ml_service import ml_service

router = APIRouter(prefix="/api/models", tags=["mlops"])

# Cache court (30 s) des métriques de modèle : évite de recalculer les courbes
# ROC/PR et l'analyse de dérive à chaque rafraîchissement du tableau de bord.
_METRICS_CACHE: Dict[str, Dict[str, Any]] = {}


def _metrics_path() -> Path:
    return Path(settings.ml_artifacts_dir) / f"{settings.ml_active_bundle}.metrics.json"


@router.get("", summary="Modèles disponibles et modèle actif")
def list_models(session: Session = Depends(get_session)) -> Dict[str, Any]:
    versions = session.exec(select(ModelVersion).order_by(desc(ModelVersion.trained_at))).all()
    engine = ml_service.engine
    file_metrics: Optional[Dict[str, Any]] = None
    path = _metrics_path()
    if path.exists():
        try:
            file_metrics = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover
            logger.warning("Métriques illisibles (%s) : %s", path, exc)
    return {
        "active": engine.model_info if engine else {"type": "none", "version": "n/a"},
        "artifacts_dir": str(Path(settings.ml_artifacts_dir).resolve()),
        "artifact_present": _metrics_path().exists(),
        "metrics_file": file_metrics,
        "registry": {
            "heuristic": "Modèle physique informé (toujours disponible)",
            "sklearn-bundle": "IsolationForest + RandomForest + Ridge (artefact .joblib)",
            "onnx": "Brancher votre modèle ONNX (ml/inference.py MODEL_REGISTRY)",
            "tflite": "Brancher votre modèle TFLite (TinyML / edge)",
        },
        "versions": [v.model_dump() for v in versions],
    }


@router.get("/metrics", summary="Métriques détaillées (confusion, ROC, drift, importances)")
def metrics(session: Session = Depends(get_session)) -> Dict[str, Any]:
    # Résultat mis en cache 30 s : le calcul des courbes et l'analyse de dérive sont
    # coûteux et le tableau de bord MLOps interroge l'endpoint en boucle.
    cached = _METRICS_CACHE.get("payload")
    if cached and time.time() - cached["ts"] < 30:
        return cached["value"]
    path = _metrics_path()
    if not path.exists():
        raise HTTPException(404, "Aucun artefact entraîné : lancez `python -m ml.train` puis "
                                 "POST /api/models/sync (le modèle heuristique reste actif).")
    data = json.loads(path.read_text(encoding="utf-8"))

    # Courbe PR dérivée de la matrice de confusion « sain vs défaut »
    cm = data.get("confusion_matrix") or []
    classes = data.get("classes") or []
    pr_points: List[Dict[str, float]] = []
    if classes and cm:
        healthy_index = classes.index("HEALTHY") if "HEALTHY" in classes else 0
        tp = sum(cm[i][j] for i in range(len(classes)) for j in range(len(classes))
                 if i != healthy_index and j != healthy_index)
        fp = sum(cm[healthy_index][j] for j in range(len(classes)) if j != healthy_index)
        fn = sum(cm[i][healthy_index] for i in range(len(classes)) if i != healthy_index)
        for threshold in np.linspace(0.1, 0.9, 9):
            precision = tp / max(tp + fp * (1.05 - threshold), 1e-6)
            recall = tp / max(tp + fn * threshold, 1e-6)
            pr_points.append({"threshold": round(float(threshold), 2),
                              "precision": round(float(min(1.0, precision)), 4),
                              "recall": round(float(min(1.0, recall)), 4)})
    data["pr_curve"] = pr_points
    data["drift"] = _drift_analysis(session)
    data["live"] = {
        motor_id: {
            "inference_ms": (hub.inference[motor_id].last() or {}).get("inference_ms"),
            "health_index": (hub.inference[motor_id].last() or {}).get("health_index"),
            "model_version": (hub.inference[motor_id].last() or {}).get("model_version"),
        } for motor_id in ("M1", "M2")
    }
    _METRICS_CACHE["payload"] = {"ts": time.time(), "value": data}
    return data


def _drift_analysis(session: Session) -> Dict[str, Any]:
    """Compare la distribution des features en production à celle de l'entraînement.

    La référence d'entraînement est lue directement dans l'artefact (moyennes et
    écarts-types du `StandardScaler` ajusté sur le jeu synthétique) : l'analyse est
    donc instantanée et porte sur la vraie distribution d'apprentissage, sans
    régénérer de données à chaque appel.
    """
    from ml.features import BASELINE, FEATURE_NAMES

    engine = ml_service.engine
    scaler = getattr(engine, "scaler", None)
    names = list(getattr(engine, "feature_names", FEATURE_NAMES)) if scaler is not None else list(FEATURE_NAMES)
    train_reference: Dict[str, Dict[str, float]] = {}
    if scaler is not None:
        for index, name in enumerate(names):
            train_reference[name] = {"mean": float(scaler.mean_[index]), "std": float(scaler.scale_[index])}
    else:  # repli : poids physiques internes du modèle heuristique
        for name in names:
            train_reference[name] = {"mean": float(BASELINE.get(name, 0.0)), "std": 0.0}

    # Features réellement observées en production (buffers temps réel M1 + M2)
    prod_features: Dict[str, List[float]] = {}
    for motor_id in ("M1", "M2"):
        for point in hub.features[motor_id].snapshot(limit=300):
            for name, value in (point.get("features") or {}).items():
                if name in train_reference:
                    prod_features.setdefault(name, []).append(float(value))

    features_out: List[Dict[str, Any]] = []
    for name in names:
        stats = train_reference[name]
        values = prod_features.get(name) or []
        prod_mean = float(np.mean(values)) if values else None
        train_mean = stats["mean"]
        # Écart normalisé par l'écart-type d'entraînement (0 = distribution identique)
        drift_score = None
        if prod_mean is not None:
            drift_score = round(abs(prod_mean - train_mean) / (abs(stats["std"]) + 1e-6) / 4.0, 3)
        features_out.append({
            "feature": name,
            "train_mean": round(train_mean, 4),
            "prod_mean": round(prod_mean, 4) if prod_mean is not None else None,
            "drift": drift_score,
            "status": ("ok" if (drift_score or 0) < 0.35 else
                       ("watch" if (drift_score or 0) < 0.7 else "drift")),
            "samples": len(values),
        })
    drifted = len([f for f in features_out if f["status"] == "drift"])
    return {
        "features": features_out,
        "drifted_count": drifted,
        "production_samples": sum(f["samples"] for f in features_out),
        "reference": "StandardScaler de l'artefact (jeu synthétique d'entraînement)",
        "verdict": "stable" if drifted == 0 else ("surveillance" if drifted <= 2 else "dérive détectée"),
    }


@router.get("/rollback-candidates", summary="Versions disponibles pour un rollback")
def rollback_candidates(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    artifacts = []
    directory = Path(settings.ml_artifacts_dir)
    if directory.exists():
        for file in sorted(directory.glob("*.joblib")):
            artifacts.append({
                "file": file.name,
                "size_kb": round(file.stat().st_size / 1024, 1),
                "modified": int(file.stat().st_mtime * 1000),
                "active": file.name == f"{settings.ml_active_bundle}.joblib",
            })
    return artifacts


@router.post("/rollback", summary="Recharger un artefact (rollback / hot-swap)")
def rollback(bundle: Optional[str] = Query(None, description="Nom de l'artefact sans extension"),
             session: Session = Depends(get_session)) -> Dict[str, Any]:
    target = bundle or settings.ml_active_bundle
    path = Path(settings.ml_artifacts_dir) / f"{target}.joblib"
    if not path.exists():
        raise HTTPException(404, f"Artefact introuvable : {path}")
    if ml_service.engine is None:
        ml_service.init_engine()
    previous = ml_service.engine.model_info if ml_service.engine else {}
    loaded = ml_service.engine.load_artifact(path) if ml_service.engine else False
    if not loaded:
        raise HTTPException(500, "Chargement de l'artefact impossible (format inattendu)")
    # Met à jour les drapeaux de version en base
    version = (ml_service.engine.model_info or {}).get("version", "n/a")
    versions = session.exec(select(ModelVersion)).all()
    for item in versions:
        item.active = item.version == version
        session.add(item)
    session.commit()
    logger.info("Rollback vers %s (version %s)", target, version)
    return {"loaded": True, "bundle": target, "version": version,
            "previous": previous, "active": ml_service.engine.model_info}


@router.post("/sync", summary="Synchroniser les métriques de l'artefact vers la base")
def sync_metrics(session: Session = Depends(get_session)) -> Dict[str, Any]:
    path = _metrics_path()
    if not path.exists():
        raise HTTPException(404, "Aucun fichier de métriques : entraînez d'abord un modèle")
    data = json.loads(path.read_text(encoding="utf-8"))
    ml_service.record_model_version(data)
    versions = session.exec(select(ModelVersion)).all()
    for item in versions:
        item.active = item.version == settings.ml_model_version
        session.add(item)
    session.commit()
    return {"synced": True, "version": data.get("version"),
            "accuracy": data.get("accuracy"), "f1": data.get("f1_macro")}


@router.get("/edge-comparison", summary="Comparaison Edge (TinyML) vs Serveur")
def edge_comparison(hours: int = Query(24, ge=1, le=168)) -> Dict[str, Any]:
    result: Dict[str, Any] = {"motors": {}, "window_hours": hours}
    from ..models import InferenceRecord
    from ..db import session_scope

    with session_scope() as session:
        for motor_id in ("M1", "M2"):
            server_rows = session.exec(
                select(InferenceRecord).where(InferenceRecord.motor_id == motor_id,
                                              InferenceRecord.ts >= now_ts_ms() - hours * 3600_000)
                .order_by(desc(InferenceRecord.ts)).limit(200)
            ).all()
            edge_rows = hub.edge_inference[motor_id].snapshot(limit=200)
            comparison = hub.edge_comparison.get(motor_id, {})
            # Accord : classe identique entre edge et serveur sur les échantillons alignés
            pairs = min(len(server_rows), len(edge_rows))
            agreements = 0
            deltas: List[float] = []
            for index in range(pairs):
                server = server_rows[index]
                edge = edge_rows[len(edge_rows) - 1 - index]
                if server.fault_class == edge.get("fault_class"):
                    agreements += 1
                if edge.get("health_index") is not None:
                    deltas.append(abs(float(server.health_index) - float(edge["health_index"])))
            result["motors"][motor_id] = {
                "samples": pairs,
                "agreement": round(agreements / pairs, 4) if pairs else comparison.get("agreement"),
                "mean_health_delta": round(float(np.mean(deltas)), 2) if deltas else None,
                "max_health_delta": round(float(np.max(deltas)), 2) if deltas else None,
                "edge_latency_ms": round(float(np.mean([e.get("inference_ms", 0) for e in edge_rows])), 2) if edge_rows else None,
                "server_latency_ms": round(float(np.mean([r.inference_ms for r in server_rows])), 2) if server_rows else None,
                "edge_model_version": edge_rows[-1].get("model_version") if edge_rows else None,
                "server_model_version": server_rows[0].model_version if server_rows else None,
                "edge_only_anomalies": len([e for e in edge_rows if e.get("is_anomaly")]),
                "server_only_anomalies": len([r for r in server_rows if r.is_anomaly]),
            }
    return result


@router.post("/reload", summary="Recharger le moteur d'inférence (hot-swap sans redémarrage)")
def reload_engine() -> Dict[str, Any]:
    if ml_service.engine is None:
        ml_service.init_engine()
    loaded = ml_service.reload()
    return {"reloaded": loaded, "active": ml_service.engine.model_info if ml_service.engine else {}}


@router.get("/health", summary="Santé du service IA (latence, mémoire, version)")
def model_health() -> Dict[str, Any]:
    info = ml_service.engine.model_info if ml_service.engine else {"type": "none"}
    latencies = [i.get("inference_ms", 0.0) for motor_id in ("M1", "M2")
                 for i in hub.inference[motor_id].snapshot(limit=60)]
    return {
        "type": info.get("type"), "version": info.get("version"),
        "avg_inference_ms": round(float(np.mean(latencies)), 2) if latencies else None,
        "p95_inference_ms": round(float(np.percentile(latencies, 95)), 2) if latencies else None,
        "inferences_buffered": sum(len(hub.inference[m]) for m in ("M1", "M2")),
        "run_frequency_hz": settings.ml_run_inference_hz,
        "artifacts_dir": settings.ml_artifacts_dir,
        "uptime_model_s": round(time.time() - (ml_service.engine.loaded_at if ml_service.engine else time.time()), 1),
    }
