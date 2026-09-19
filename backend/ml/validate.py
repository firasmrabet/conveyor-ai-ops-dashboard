"""
Validation d'un artefact de modèle — utilisée par le build Docker et par l'entrypoint.

    python -m ml.validate                       # valide ./artifacts/conveyor-synth.joblib
    python -m ml.validate --bundle autre-modele

Contrôles effectués :
  1. le fichier existe et se charge avec la version de scikit-learn installée,
  2. les 21 caractéristiques attendues sont présentes,
  3. une trame nominale est classée HEALTHY avec une santé ≥ 85/100,
  4. une trame de défaut franc (roulement) est classée BEARING_FAULT avec un RUL < 3000 h.

Code de sortie : 0 = artefact exploitable, 1 = à réentraîner.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from ml.features import FEATURE_NAMES  # noqa: E402


def validate(bundle_path: Path) -> bool:
    try:
        import joblib

        from ml.model import ConveyorAIEngine
        from ml.physics import MotorPhysics
        from ml.synthetic import telemetry_from_physics
        from ml.features import extract_from_telemetry

        bundle = joblib.load(bundle_path)
        names = list(bundle.get("feature_names") or [])
        missing = [n for n in FEATURE_NAMES if n not in names]
        if missing:
            print(f"[validate] caractéristiques manquantes : {missing}", file=sys.stderr)
            return False

        engine = ConveyorAIEngine(artifacts_dir=str(bundle_path.parent),
                                  bundle_name=bundle_path.stem)
        if engine.model_info["type"] != "sklearn-bundle":
            print("[validate] l'artefact n'a pas été chargé par le moteur", file=sys.stderr)
            return False

        def sample(fault: str = "NONE", intensity: float = 0.0):
            phys = MotorPhysics(motor_id="M1", target_rpm=120.0, load_kg=0.6, seed=3)
            if fault != "NONE":
                phys.inject_fault(fault, intensity, degradation_rate=0.0)
            phys.rpm = 120.0
            phys.settle_thermal(current_rms=0.83, seconds=900.0, dt=15.0)
            now, dt, telemetry, window = 0.0, 0.125, {}, []
            for _ in range(60):
                now += dt
                telemetry = telemetry_from_physics(phys, now, dt, 120.0)
                telemetry["target_speed_rpm"] = 120.0
                window.append(telemetry["speed"]["rpm"])
            return extract_from_telemetry(telemetry, None, window)

        healthy = engine.predict("M1", sample(), 0, 0.01)
        faulty = engine.predict("M1", sample("BEARING_FAULT", 70.0), 0, 0.01)
        print(f"[validate] nominal : {healthy['fault_class']} / santé {healthy['health_index']:.1f} "
              f"/ anomalie {healthy['anomaly_score']:.2f}")
        print(f"[validate] roulement : {faulty['fault_class']} / santé {faulty['health_index']:.1f} "
              f"/ RUL {faulty['rul_hours']:.0f} h")
        ok = (healthy["fault_class"] == "HEALTHY" and healthy["health_index"] >= 85.0
              and faulty["fault_class"] == "BEARING_FAULT" and faulty["rul_hours"] < 3000.0)
        if not ok:
            print("[validate] comportement du modèle hors des attentes", file=sys.stderr)
        return ok
    except Exception as exc:  # pragma: no cover - dépend de l'environnement
        print(f"[validate] artefact inutilisable : {exc}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Validation d'un artefact de modèle")
    parser.add_argument("--artifacts", default="./artifacts")
    parser.add_argument("--bundle", default="conveyor-synth")
    args = parser.parse_args()
    path = Path(args.artifacts) / f"{args.bundle}.joblib"
    if not path.exists():
        print(f"[validate] artefact absent : {path}", file=sys.stderr)
        return 1
    return 0 if validate(path) else 1


if __name__ == "__main__":
    raise SystemExit(main())
