"""Chargement des modèles + inférence unitaire (CLI de vérification).

Exemples :
    python -m ml.inference --model M1 --fault BEARING_FAULT --intensity 70
    python -m ml.inference --telemetry data/sample_telemetry.json
    python -m ml.inference --bundle ./artifacts/conveyor-synth.joblib --selftest

Ce module est le point unique à modifier pour brancher VOS modèles :
soit vous produisez un artefact `.joblib` au même format que `ml/train.py`,
soit vous exposez une classe avec la méthode `predict(feats, health_history, wear)`
et la référencez dans `MODEL_REGISTRY`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.append(str(Path(__file__).resolve().parents[1]))

from ml.features import extract_from_telemetry  # noqa: E402
from ml.model import ConveyorAIEngine  # noqa: E402
from ml.physics import MotorPhysics  # noqa: E402
from ml.synthetic import telemetry_from_physics  # noqa: E402

# Registre : nom logique → classe. Ajoutez vos propres modèles ici.
MODEL_REGISTRY: Dict[str, str] = {
    "heuristic": "ml.model.HeuristicModel",
    "sklearn-bundle": "ml.model.SklearnBundle",
    "onnx": "votre_module.OnnxModel",          # exemple à implémenter de votre côté
    "tflite": "votre_module.TFLiteModel",      # exemple à implémenter de votre côté
}


def load_engine(artifacts_dir: str = "./artifacts", bundle: str = "conveyor-synth") -> ConveyorAIEngine:
    engine = ConveyorAIEngine(artifacts_dir=artifacts_dir, bundle_name=bundle)
    info = engine.model_info
    print(f"[inference] modèle actif : {info['type']} {info['version']} "
          f"{'(' + info['path'] + ')' if info['path'] else '(poids physiques internes)'}")
    return engine


def predict_from_telemetry(engine: ConveyorAIEngine, telemetry: Dict[str, Any]) -> Dict[str, Any]:
    feats = telemetry.get("features") or extract_from_telemetry(telemetry)
    motor_id = telemetry.get("motor_id", "M1")
    return engine.predict(motor_id, feats, telemetry.get("ts"))


def simulate_and_predict(fault: str = "NONE", intensity: float = 50.0, motor: str = "M1",
                         engine: ConveyorAIEngine | None = None) -> Dict[str, Any]:
    """Fait tourner la physique 20 s puis infère : sert de test de fumée."""
    engine = engine or load_engine()
    phys = MotorPhysics(motor_id=motor, target_rpm=120, load_kg=0.6, seed=3)
    if fault != "NONE":
        phys.inject_fault(fault, intensity, degradation_rate=0.0)
    # Régime thermique établi AVANT l'extraction (comme dans le dataset d'entraînement)
    phys.rpm = 120.0
    phys.settle_thermal(current_rms=0.28 + 0.55 * 0.6 + 0.5, seconds=900.0, dt=15.0)
    now, dt = 0.0, 0.125
    tel: Dict[str, Any] = {}
    rpm_window = []
    for _ in range(80):
        now += dt
        tel = telemetry_from_physics(phys, now, dt, 120)
        rpm_window.append(tel["speed"]["rpm"])
    feats = extract_from_telemetry(tel, None, rpm_window)
    return engine.predict(motor, feats, tel["ts"])


def _selftest(engine: ConveyorAIEngine) -> None:
    cases = [
        ("NONE", 0.0), ("BEARING_FAULT", 70.0), ("UNBALANCE", 65.0), ("MISALIGNMENT", 60.0),
        ("BELT_SLIP", 55.0), ("OVERLOAD", 70.0), ("OVERHEATING", 60.0),
    ]
    ok = 0
    print(f"\n{'défaut injecté':<18} {'classe IA':<16} {'santé':>6} {'anomalie':>9} {'RUL (h)':>9} {'ms':>6}")
    print("-" * 70)
    for fault, intensity in cases:
        out = simulate_and_predict(fault, intensity, engine=engine)
        expected = "HEALTHY" if fault == "NONE" else fault
        verdict = "OK " if out["fault_class"] == expected else "≈  "
        if out["fault_class"] == expected:
            ok += 1
        print(f"{fault:<18} {out['fault_class']:<16} {out['health_index']:>6.1f} "
              f"{out['anomaly_score']:>9.3f} {out['rul_hours']:>9.1f} {out['inference_ms']:>6.2f}  {verdict}")
    print(f"\n[selftest] {ok}/{len(cases)} cas correctement classés "
          f"({ok / len(cases) * 100:.0f} %).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inférence unitaire Conveyor AI Ops")
    parser.add_argument("--artifacts", default="./artifacts")
    parser.add_argument("--bundle", default="conveyor-synth")
    parser.add_argument("--model", default="M1", choices=["M1", "M2"])
    parser.add_argument("--fault", default="BEARING_FAULT")
    parser.add_argument("--intensity", type=float, default=60.0)
    parser.add_argument("--telemetry", help="fichier JSON d'un payload de télémétrie")
    parser.add_argument("--selftest", action="store_true", help="test de fumée sur tous les défauts")
    args = parser.parse_args()

    engine = load_engine(args.artifacts, args.bundle)
    if args.selftest:
        _selftest(engine)
    elif args.telemetry:
        payload = json.loads(Path(args.telemetry).read_text(encoding="utf-8"))
        print(json.dumps(predict_from_telemetry(engine, payload), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(simulate_and_predict(args.fault, args.intensity, args.model, engine),
                         indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
