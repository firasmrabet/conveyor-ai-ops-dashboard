"""Entraînement des modèles (anomalie + classification de défaut + RUL).

Usage :
    python -m ml.train                       # entraînement complet
    python -m ml.train --quick               # jeu réduit (build Docker / CI)
    python -m ml.train --samples 400 --out ./artifacts --version v1.4.0

Sorties :
    artifacts/conveyor-synth.joblib   → scaler + IsolationForest + RandomForest + Ridge
    artifacts/conveyor-synth.metrics.json → métriques (accuracy, F1, AUC, MAE RUL, matrice de confusion)

Les modèles sont entraînés sur des données SYNTHÉTIQUES issues du modèle
physique du convoyeur. Pour brancher vos modèles entraînés sur PC, cf. README.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))

from ml.features import FEATURE_NAMES  # noqa: E402
from ml.model import FAULT_CLASSES  # noqa: E402
from ml.synthetic import generate_dataset  # noqa: E402


def train(samples_per_class: int = 120, out_dir: str = "./artifacts", version: str = "v1.4.0",
          seed: int = 7, quick: bool = False) -> Dict[str, Any]:
    from sklearn.ensemble import IsolationForest, RandomForestClassifier, GradientBoostingRegressor
    from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score, mean_absolute_error,
                                 precision_score, recall_score, roc_auc_score)
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler
    import joblib

    if quick:
        samples_per_class = min(samples_per_class, 40)

    print(f"[train] génération du dataset synthétique ({samples_per_class} éch./classe)…")
    X, y, rul = generate_dataset(samples_per_class=samples_per_class, seed=seed)
    print(f"[train] dataset : {X.shape[0]} échantillons × {X.shape[1]} features")
    print(f"[train] classes : { {c: int((y == c).sum()) for c in sorted(set(y))} }")

    X_tr, X_te, y_tr, y_te, rul_tr, rul_te = train_test_split(
        X, y, rul, test_size=0.25, random_state=seed, stratify=y
    )

    scaler = StandardScaler().fit(X_tr)
    Xs_tr, Xs_te = scaler.transform(X_tr), scaler.transform(X_te)

    # --- 1. Détection d'anomalie (non supervisé, entraîné sur le sain uniquement)
    healthy_tr = Xs_tr[y_tr == "HEALTHY"]
    iso = IsolationForest(
        n_estimators=200, contamination=0.08, random_state=seed, n_jobs=-1
    ).fit(healthy_tr)
    healthy_scores = -iso.score_samples(healthy_tr)
    iso_offset = float(np.mean(healthy_scores))
    iso_scale = float(np.std(healthy_scores)) or 1.0

    # --- 2. Classification du type de défaut
    clf = RandomForestClassifier(
        n_estimators=320, max_depth=16, min_samples_leaf=2, random_state=seed,
        n_jobs=-1, class_weight="balanced_subsample",
    ).fit(Xs_tr, y_tr)

    # --- 3. Régression du RUL
    reg = GradientBoostingRegressor(
        n_estimators=320, learning_rate=0.05, max_depth=3, subsample=0.9, random_state=seed
    ).fit(Xs_tr, np.log1p(rul_tr))

    # ---------------------- Évaluation ----------------------
    started = time.perf_counter()
    _ = clf.predict_proba(Xs_te[:1])
    latency_ms = (time.perf_counter() - started) * 1000
    y_pred = clf.predict(Xs_te)
    y_proba = clf.predict_proba(Xs_te)
    classes = list(clf.classes_)
    healthy_idx = classes.index("HEALTHY") if "HEALTHY" in classes else 0
    # ROC one-vs-rest « sain vs défaut »
    y_bin = (y_te != "HEALTHY").astype(int)
    score_bin = 1.0 - y_proba[:, healthy_idx]
    try:
        auc = float(roc_auc_score(y_bin, score_bin)) if len(set(y_bin)) > 1 else float("nan")
    except Exception:
        auc = float("nan")

    rul_pred = np.expm1(reg.predict(Xs_te))
    roc_points = []
    if len(set(y_bin)) > 1:
        from sklearn.metrics import roc_curve
        fpr, tpr, _ = roc_curve(y_bin, score_bin)
        step = max(1, len(fpr) // 40)
        roc_points = [{"fpr": round(float(f), 4), "tpr": round(float(t), 4)}
                      for f, t in zip(fpr[::step], tpr[::step])]

    cm = confusion_matrix(y_te, y_pred, labels=classes).tolist()
    metrics = {
        "version": version,
        "trained_at": int(time.time() * 1000),
        "dataset_size": int(X.shape[0]),
        "samples_per_class": samples_per_class,
        "features": FEATURE_NAMES,
        "classes": classes,
        "accuracy": round(float(accuracy_score(y_te, y_pred)), 4),
        "f1_macro": round(float(f1_score(y_te, y_pred, average="macro")), 4),
        "precision_macro": round(float(precision_score(y_te, y_pred, average="macro", zero_division=0)), 4),
        "recall_macro": round(float(recall_score(y_te, y_pred, average="macro", zero_division=0)), 4),
        "roc_auc": None if np.isnan(auc) else round(auc, 4),
        "roc_curve": roc_points,
        "rul_mae_hours": round(float(mean_absolute_error(rul_te, rul_pred)), 2),
        "rul_mape_pct": round(float(np.mean(np.abs((rul_te - rul_pred) / np.maximum(rul_te, 1e-6))) * 100), 2),
        "confusion_matrix": cm,
        "inference_ms": round(latency_ms, 3),
        "feature_importances": {
            n: round(float(v), 4) for n, v in sorted(
                zip(FEATURE_NAMES, clf.feature_importances_), key=lambda kv: -kv[1])
        },
        "isolation_forest": {"offset": iso_offset, "scale": iso_scale, "contamination": 0.08},
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bundle_path = out / "conveyor-synth.joblib"
    joblib.dump(
        {
            "scaler": scaler,
            "isolation_forest": iso,
            "classifier": clf,
            "rul_model": reg,
            "feature_names": FEATURE_NAMES,
            "version": version,
            "iso_offset": iso_offset,
            "iso_scale": iso_scale,
            "metadata": {k: metrics[k] for k in
                         ("accuracy", "f1_macro", "roc_auc", "rul_mae_hours", "dataset_size", "trained_at")},
        },
        bundle_path,
        compress=3,
    )
    size_kb = round(bundle_path.stat().st_size / 1024, 1)
    metrics["artifact_kb"] = size_kb
    (out / "conveyor-synth.metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print(f"[train] accuracy={metrics['accuracy']}  f1={metrics['f1_macro']}  auc={metrics['roc_auc']}  "
          f"RUL MAE={metrics['rul_mae_hours']} h")
    print(f"[train] artefact écrit : {bundle_path} ({size_kb} Ko)")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Entraînement des modèles Conveyor AI Ops")
    parser.add_argument("--samples", type=int, default=120, help="échantillons par classe")
    parser.add_argument("--out", type=str, default="./artifacts", help="dossier de sortie")
    parser.add_argument("--version", type=str, default="v1.4.0", help="version du modèle")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--quick", action="store_true", help="jeu réduit (build/CI)")
    args = parser.parse_args()
    train(samples_per_class=args.samples, out_dir=args.out, version=args.version,
          seed=args.seed, quick=args.quick)


if __name__ == "__main__":
    main()
