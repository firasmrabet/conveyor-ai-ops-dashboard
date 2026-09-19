#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Amorçage et vérification de la base — Conveyor AI Ops
=====================================================

Ce script lit `data/seed.json` (description déclarative du banc : actifs, seuils,
comptes, modèles, scénario) et l'applique à la base SQLite/SQLModel du backend.

Deux façons de l'utiliser
-------------------------
1. **Par la base** (défaut, fonctionne sans backend démarré) :
       python scripts/seed_db.py --apply
2. **Par l'API REST** (le backend doit tourner, utile après un `docker compose up`) :
       python scripts/seed_db.py --apply --api http://localhost:8000 --token <JWT>

Autres usages
-------------
    # Vérifier une installation fraîche par rapport aux valeurs de référence
    python scripts/seed_db.py --check

    # Réinitialiser complètement les données de démonstration avant une soutenance
    python scripts/seed_db.py --reset --apply --history-hours 24

    # Afficher ce qui serait fait, sans rien écrire
    python scripts/seed_db.py --apply --dry-run

Le script est **idempotent** : relancé, il met à jour les enregistrements existants
sans créer de doublons (les moteurs, comptes, règles et modèles sont identifiés par
leur clé métier) et ne touche jamais à la télémétrie sauf avec `--reset`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- Chemins du projet -------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))          # pour importer `app.*` et `ml.*`

DEFAULT_SEED_FILE = ROOT / "data" / "seed.json"


# =============================================================================
#  Chargement du fichier de description du banc
# =============================================================================
def load_seed(path: Path) -> Dict[str, Any]:
    """Charge et valide grossièrement `seed.json` (sections obligatoires)."""
    if not path.exists():
        raise SystemExit(f"[erreur] fichier introuvable : {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[erreur] JSON invalide dans {path} : {exc}")
    required = ("motors", "gateway", "users", "thresholds", "models", "settings")
    missing = [key for key in required if key not in data]
    if missing:
        raise SystemExit(f"[erreur] sections manquantes dans {path} : {', '.join(missing)}")
    return data


# =============================================================================
#  Application à la base (SQLModel, sans serveur)
# =============================================================================
def apply_to_database(seed: Dict[str, Any], dry_run: bool = False,
                      history_hours: int = 0, reset: bool = False) -> Dict[str, Any]:
    """Écrit la description du banc dans la base du backend.

    Args:
        seed: contenu de `data/seed.json`.
        dry_run: n'écrit rien, se contente de compter les opérations.
        history_hours: si > 0, régénère en plus un historique de démonstration.
        reset: supprime les données de démonstration avant application
               (télémétrie, inférences, alertes, ordres de travail, interventions).

    Returns:
        Un dictionnaire de statistiques (créés / mis à jour / supprimés).
    """
    from sqlmodel import Session, delete, select

    from app.db import engine, init_db
    from app.models import (Alert, AlertRule, AppSetting, Device, HealthSnapshot, HourlyAggregate,
                            InferenceRecord, Intervention, ModelVersion, Motor, TelemetryRecord,
                            User, WorkOrder, now_ts_ms)
    from app.security import hash_password
    from app.services.seed import seed_all

    stats: Dict[str, Any] = {"motors": 0, "users": 0, "rules_checked": 0, "device": 0,
                             "settings": 0, "models": 0, "reset": 0, "history": None}

    init_db()
    if reset and not dry_run:
        with Session(engine) as session:
            for model in (TelemetryRecord, InferenceRecord, HealthSnapshot, HourlyAggregate,
                          Alert, WorkOrder, Intervention):
                session.exec(delete(model))
            # Une seule ligne conservée par moteur : le suivi d'usure est remis à zéro
            for motor in session.exec(select(Motor)).all():
                motor.running_hours = 0.0
                motor.maintenance_count = 0
                session.add(motor)
            session.commit()
            stats["reset"] = 1
            print("[seed] Données de démonstration réinitialisées (télémétrie, alertes, ordres de travail).")

    if dry_run:
        print("[seed] --dry-run : aucune écriture effectuée.")
        stats["motors"] = len(seed["motors"])
        stats["users"] = len(seed["users"])
        stats["settings"] = len(seed["settings"])
        stats["models"] = len(seed["models"])
        return stats

    # --- 1) Moteurs (clé métier : id) ---------------------------------------
    with Session(engine) as session:
        for motor_data in seed["motors"]:
            motor = session.get(Motor, motor_data["id"])
            if motor is None:
                motor = Motor(id=motor_data["id"])
                stats["motors"] += 1
            # Les valeurs du fichier font foi : c'est la fiche technique du banc
            motor.name = motor_data.get("name", motor.id)
            motor.label = motor_data.get("label", "")
            motor.side = motor_data.get("side", "LEFT")
            motor.model = motor_data.get("model", motor.model)
            motor.nominal_current_a = float(motor_data.get("nominal_current_a", 1.2))
            motor.nominal_rpm = float(motor_data.get("nominal_rpm", 120.0))
            motor.nominal_temp_c = float(motor_data.get("nominal_temp_c", 45.0))
            motor.baseline = dict(motor_data.get("baseline", {}))
            motor.running_hours = float(motor_data.get("running_hours", motor.running_hours))
            motor.maintenance_count = int(motor_data.get("maintenance_count", motor.maintenance_count))
            session.add(motor)
        session.commit()
    print(f"[seed] Moteurs appliqués : {', '.join(m['id'] for m in seed['motors'])}")

    # --- 2) Comptes utilisateurs (clé métier : email) -----------------------
    with Session(engine) as session:
        for user_data in seed["users"]:
            user = session.exec(select(User).where(User.email == user_data["email"])).first()
            if user is None:
                user = User(email=user_data["email"])
                stats["users"] += 1
            user.full_name = user_data.get("full_name", "")
            user.role = user_data.get("role", "viewer")
            user.active = True
            # Le mot de passe n'est réécrit que s'il est fourni (permet de garder
            # un secret modifié en production tout en amorçant une base neuve).
            if user_data.get("password"):
                user.password_hash = hash_password(user_data["password"])
            session.add(user)
        session.commit()
    print(f"[seed] Comptes : {stats['users']} créé(s), {len(seed['users']) - stats['users']} mis à jour.")

    # --- 3) Passerelle ESP32 (clé métier : device_id) -----------------------
    gateway = seed["gateway"]
    with Session(engine) as session:
        device = session.exec(select(Device).where(Device.device_id == gateway["device_id"])).first()
        if device is None:
            device = Device(device_id=gateway["device_id"])
            stats["device"] += 1
        device.name = gateway.get("name", device.device_id)
        device.ip = gateway.get("ip", device.ip)
        device.mac = gateway.get("mac", device.mac)
        device.firmware = gateway.get("firmware", device.firmware)
        device.power_source = gateway.get("power_source", device.power_source)
        device.sampling_hz = float(gateway.get("sampling_hz", device.sampling_hz))
        device.status = "ONLINE"
        device.metrics = {
            "board": gateway.get("board"),
            "telemetry_hz": gateway.get("telemetry_hz"),
            "fft_size": gateway.get("fft_size"),
            "mqtt": gateway.get("mqtt"),
            "sensors": gateway.get("sensors"),
        }
        session.add(device)
        session.commit()
    print(f"[seed] Passerelle {gateway['device_id']} : {'créée' if stats['device'] else 'mise à jour'} "
          f"({len(gateway.get('sensors', []))} capteurs décrits).")

    # --- 4) Règles d'alerte -------------------------------------------------
    # Les 9 règles de référence viennent de `ml/model.py` (source unique de vérité).
    # On y ajoute/ajuste les seuils décrits dans seed.json pour rester aligné sur le banc.
    base_stats = seed_all()
    stats["rules_checked"] = base_stats.get("rules", 0)
    thresholds = seed.get("thresholds", {})
    metric_mapping = {
        "vibration_rms": "vibration_rms", "vibration_peak": "vibration_peak",
        "kurtosis": "kurtosis", "current_rms": "current_rms",
        "bearing_temp": "bearing_temp", "motor_temp": "motor_temp",
        "health_index": "health_index", "anomaly_score": "anomaly_score",
    }
    with Session(engine) as session:
        for seed_key, metric in metric_mapping.items():
            values = thresholds.get(seed_key, {})
            if not values:
                continue
            rule = session.exec(select(AlertRule).where(AlertRule.metric == metric)).first()
            if rule is None:
                continue
            if "warning" in values:
                if rule.severity == "CRITICAL" and values.get("critical") is not None:
                    rule.threshold = float(values["critical"])
                else:
                    rule.threshold = float(values["warning"])
            if "hysteresis" in values:
                rule.hysteresis = float(values["hysteresis"])
            session.add(rule)
        session.commit()
    print(f"[seed] Règles d'alerte : {stats['rules_checked']} présentes, seuils alignés sur seed.json.")

    # --- 5) Paramètres applicatifs -----------------------------------------
    with Session(engine) as session:
        for key, value in seed["settings"].items():
            setting = session.get(AppSetting, key)
            if setting is None:
                setting = AppSetting(key=key)
                stats["settings"] += 1
            setting.value = value
            setting.updated_at = now_ts_ms()
            session.add(setting)
        session.commit()
    print(f"[seed] Paramètres : {stats['settings']} créé(s), "
          f"{len(seed['settings']) - stats['settings']} mis à jour.")

    # --- 6) Versions de modèles (page /ml, rollback) -----------------------
    with Session(engine) as session:
        for model_data in seed["models"]:
            model = session.exec(select(ModelVersion).where(
                ModelVersion.name == model_data["name"],
                ModelVersion.version == model_data["version"])).first()
            if model is None:
                model = ModelVersion(name=model_data["name"], version=model_data["version"])
                stats["models"] += 1
            model.kind = model_data.get("kind", "server")
            model.size_kb = float(model_data.get("size_kb", 0.0))
            model.latency_ms = float(model_data.get("latency_ms", 0.0))
            model.accuracy = float(model_data.get("accuracy", 0.0))
            model.f1 = float(model_data.get("f1", 0.0))
            model.precision = float(model_data.get("precision", model_data.get("accuracy", 0.0)))
            model.recall = float(model_data.get("recall", model_data.get("accuracy", 0.0)))
            model.roc_auc = float(model_data.get("roc_auc", 0.0))
            model.rul_mae = float(model_data.get("rul_mae", 0.0))
            model.active = bool(model_data.get("active", False))
            model.notes = model_data.get("notes", "")
            model.metrics = {"source": "data/seed.json", "applied_at": now_ts_ms()}
            session.add(model)
        session.commit()
    print(f"[seed] Modèles : {stats['models']} créé(s), "
          f"{len(seed['models']) - stats['models']} mis à jour.")

    # --- 7) Historique de démonstration (optionnel) -------------------------
    if history_hours > 0:
        with Session(engine) as session:
            history = _seed_history(session, hours=history_hours)
        stats["history"] = history
        print(f"[seed] Historique régénéré : {history}")

    return stats


def _seed_history(session: Any, hours: int = 24) -> Dict[str, int]:
    """Délègue au module d'amorçage du backend (même scénario que le premier démarrage)."""
    from app.services.seed import _seed_history as backend_seed_history  # noqa: SLF001

    return backend_seed_history(session, hours=hours)


# =============================================================================
#  Application via l'API REST (backend démarré)
# =============================================================================
def apply_via_api(seed: Dict[str, Any], api: str, token: Optional[str]) -> Dict[str, Any]:
    """Applique ce qui est exposé par l'API : seuils (règles) et paramètres.

    Les moteurs, utilisateurs, devices et modèles sont créés automatiquement par
    `seed_all()` au démarrage du backend : via l'API, on se contente donc de
    synchroniser les seuils et les paramètres, puis de vérifier la cohérence.
    """
    import urllib.error
    import urllib.request

    def call(path: str, payload: Optional[Dict[str, Any]] = None, method: str = "GET") -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(api.rstrip("/") + path, data=data, method=method,
                                         headers={"Content-Type": "application/json",
                                                  **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read().decode()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"[erreur] {method} {path} → HTTP {exc.code} : {exc.read().decode()[:200]}")

    stats: Dict[str, Any] = {"rules_updated": 0, "settings_updated": 0}

    # Seuils : on retrouve la règle par métrique puis on met à jour sa valeur
    rules = call("/api/rules")
    by_metric = {rule.get("metric"): rule for rule in rules}
    for seed_key, values in seed.get("thresholds", {}).items():
        rule = by_metric.get(seed_key)
        if not rule or "warning" not in values:
            continue
        threshold = float(values.get("critical", values["warning"])) if rule.get("severity") == "CRITICAL" \
            else float(values["warning"])
        call(f"/api/rules/{rule['id']}",
             {"threshold": threshold, "hysteresis": values.get("hysteresis", rule.get("hysteresis", 0.0))},
             method="PUT")
        stats["rules_updated"] += 1

    settings = call("/api/settings")
    current = settings.get("values", settings) if isinstance(settings, dict) else {}
    merged = {**current, **seed.get("settings", {})}
    call("/api/settings", {"values": merged}, method="PUT")
    stats["settings_updated"] = len(seed.get("settings", {}))
    return stats


# =============================================================================
#  Vérification d'une installation par rapport aux valeurs de référence
# =============================================================================
def check_installation(seed: Dict[str, Any], api: Optional[str] = None) -> int:
    """Compare l'installation courante aux valeurs de référence de seed.json.

    Returns:
        0 si tout est conforme, 1 si au moins un écart est constaté.
    """
    expected = seed.get("expected_results", {})
    report: List[str] = []
    failures = 0

    # --- 1) Le modèle actif correspond-il aux métriques de référence ? ------
    try:
        from app.config import settings as app_settings
        from app.services.ml_service import ml_service

        # Le moteur expose `engine.model_info` (nom, version, type, métadonnées) une fois
        # le cycle de vie de l'application exécuté : hors contexte serveur, on lit
        # directement les métriques écrites par l'entraînement à côté de l'artefact.
        info: Dict[str, Any] = {}
        try:
            info = ml_service.engine.model_info if ml_service.engine else {}
        except Exception:
            info = {}
        metadata = dict(info.get("metadata", {}) or {})
        if not metadata:
            # Le chemin d'artefacts est relatif au dossier backend ("./artifacts") : on
            # cherche à côté de l'artefact réel, que le script soit lancé depuis la racine
            # du projet ou depuis backend/.
            candidates = [Path(app_settings.ml_artifacts_dir) / f"{app_settings.ml_active_bundle}.metrics.json",
                          BACKEND / app_settings.ml_artifacts_dir / f"{app_settings.ml_active_bundle}.metrics.json"]
            for metrics_path in candidates:
                if metrics_path.exists():
                    metadata = json.loads(metrics_path.read_text(encoding="utf-8"))
                    break
        name = str(info.get("name", app_settings.ml_active_bundle))
        version = str(info.get("version", app_settings.ml_model_version))
        accuracy = float(metadata.get("accuracy", 0.0))
        f1 = float(metadata.get("f1_macro", metadata.get("f1", 0.0)))
        rul_mae = float(metadata.get("rul_mae_hours", 0.0))
        report.append(f"Modèle actif : {name} {version} "
                      f"(acc={accuracy:.4f}, f1={f1:.4f}, RUL MAE={rul_mae:.2f} h)")
        if accuracy and abs(accuracy - expected.get("model_accuracy", accuracy)) > 0.02:
            report.append(f"  ⚠ écart d'exactitude : attendu {expected.get('model_accuracy')}")
            failures += 1
        if rul_mae and abs(rul_mae - expected.get("rul_mae_hours", rul_mae)) > 40:
            report.append(f"  ⚠ écart de RUL MAE : attendu {expected.get('rul_mae_hours')} h")
            failures += 1
        report.append(f"  mode : {app_settings.mode} | artefacts : {app_settings.ml_artifacts_dir}")
    except Exception as exc:  # pragma: no cover - dépend du contexte d'exécution
        report.append(f"Modèle : vérification impossible hors backend ({exc})")
        failures += 1

    # --- 2) Synthèse des moteurs (attendu : HEALTHY et dans les plages de référence) --
    # Un banc qui vient de démarrer n'est pas encore à son régime thermique établi :
    # on laisse le système s'échauffer puis on compare la MÉDIANE de plusieurs relevés,
    # ce qui évite de conclure sur une valeur transitoire.
    try:
        import urllib.request

        target = (api or "http://127.0.0.1:8000").rstrip("/")

        def fetch_motors() -> List[Dict[str, Any]]:
            with urllib.request.urlopen(f"{target}/api/motors", timeout=15) as response:
                return json.loads(response.read().decode())

        warmup = int(expected.get("warmup_s", 30))
        samples = int(expected.get("samples", 4))
        interval = float(expected.get("sample_interval_s", 5))
        print(f"[check] échauffement {warmup} s puis {samples} relevés espacés de {interval:g} s…")
        time.sleep(warmup)
        history: Dict[str, List[Dict[str, Any]]] = {"M1": [], "M2": []}
        for index in range(samples):
            for motor in fetch_motors():
                history.setdefault(motor["id"], []).append(motor)
            if index < samples - 1:
                time.sleep(interval)

        healthy = expected.get("healthy_motor", {})
        for motor_id, series in history.items():
            if not series:
                continue
            health = sorted(float(m.get("health_index") or 0.0) for m in series)[len(series) // 2]
            anomaly = sorted(float(m.get("anomaly_score") or 0.0) for m in series)[len(series) // 2]
            rul = sorted(float(m.get("rul_hours") or 0.0) for m in series)[len(series) // 2]
            classes = sorted({m.get("fault_class") for m in series})
            report.append(f"{motor_id} : santé {health:.1f} · anomalie {anomaly:.3f} · "
                          f"RUL {rul:.0f} h · classes {classes} (médiane de {len(series)} relevés)")
            if classes == ["HEALTHY"]:
                if health < float(healthy.get("health_index_min", 0)):
                    report.append(f"  ⚠ santé médiane inférieure au minimum attendu "
                                  f"({healthy.get('health_index_min')})")
                    failures += 1
                if anomaly > float(healthy.get("anomaly_score_max", 1)):
                    report.append(f"  ⚠ anomalie médiane supérieure au maximum attendu "
                                  f"({healthy.get('anomaly_score_max')})")
                    failures += 1
            elif any(fault in (healthy.get("unexpected_classes") or []) for fault in classes):
                report.append("  ⚠ classes inattendues sur un banc nominal : "
                              f"{[f for f in classes if f != 'HEALTHY']}")
                failures += 1
    except Exception as exc:
        report.append(f"API REST : injoignable ({exc.__class__.__name__}) — lancer le backend pour ce contrôle")
        failures += 1

    print("\n".join(report))
    print(f"\n[check] {'CONFORME' if failures == 0 else f'{failures} ÉCART(S) À CORRIGER'}")
    return 0 if failures == 0 else 1


# =============================================================================
#  Validation dynamique : injection de défauts puis retour à la normale
# =============================================================================
def run_fault_drills(seed: Dict[str, Any], api: Optional[str] = None,
                     window_s: float = 70.0, settle_s: float = 15.0) -> int:
    """Joue les défauts de référence et vérifie la réaction du système.

    Le contrôle statique (`--check`) vérifie le régime nominal ; ce contrôle-ci
    vérifie que la chaîne complète — simulateur → télémétrie → caractéristiques →
    modèle → indice de santé → alertes — réagit bien aux défauts du catalogue, puis
    revient à l'état sain après effacement.

    Attention : les défauts sont réellement injectés dans la ligne simulée et
    génèrent de vraies alertes. À lancer sur une base de démonstration, ou suivre
    d'un `--reset --apply` pour repartir d'une base propre.

    Returns:
        0 si toutes les plages de référence sont respectées, 1 sinon.
    """
    import urllib.error
    import urllib.request

    target = (api or "http://127.0.0.1:8000").rstrip("/")
    expected = seed.get("expected_results", {})
    drills: Dict[str, Any] = expected.get("fault_drills", {})
    failures = 0

    def call(path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(f"{target}{path}", data=data,
                                         method="POST" if data is not None else "GET",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())

    def sample(motor_id: str, seconds: float) -> Dict[str, Any]:
        """Relève le moteur toutes les 6 s pendant `seconds` et résume la fenêtre."""
        readings: List[Dict[str, Any]] = []
        steps = max(1, int(seconds / 6))
        for index in range(steps):
            for motor in call("/api/motors"):
                if motor["id"] == motor_id:
                    readings.append(motor)
            if index < steps - 1:
                time.sleep(6)
        health = sorted(float(r.get("health_index") or 0.0) for r in readings)
        anomaly = sorted(float(r.get("anomaly_score") or 0.0) for r in readings)
        rul = sorted(float(r.get("rul_hours") or 0.0) for r in readings)
        return {
            "health_min": health[0],
            "anomaly_median": anomaly[len(anomaly) // 2],
            "rul_min": rul[0],
            "classes": sorted({r.get("fault_class") for r in readings}),
            "vibration_max": max(float(r.get("vibration_rms") or 0.0) for r in readings),
        }

    def within(value: float, bounds: Any) -> bool:
        if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
            return float(bounds[0]) <= value <= float(bounds[1])
        return True

    print("[drill] remise à l'état sain avant la campagne…")
    try:
        call("/api/simulator/clear-faults", {})
    except urllib.error.URLError as exc:
        print(f"[drill] backend injoignable ({exc}) — lancer le backend d'abord.")
        return 1
    time.sleep(20)

    for key, spec in drills.items():
        if "fault" not in spec:      # blocs de synthèse (récupération, stabilité…)
            continue
        motor_id = spec["motor_id"]
        call("/api/simulator/clear-faults", {})
        time.sleep(settle_s)
        call("/api/simulator/inject-fault", {"motor_id": motor_id, "fault": spec["fault"],
                                             "intensity": float(spec.get("intensity", 80.0))})
        result = sample(motor_id, window_s)
        line = (f"{spec['fault']:<12} {motor_id} {float(spec.get('intensity', 80.0)):>5.0f} % → "
                f"santé min {result['health_min']:.1f} · anomalie méd {result['anomaly_median']:.3f} · "
                f"RUL min {result['rul_min']:.0f} h · vib max {result['vibration_max']:.3f} · "
                f"classes {result['classes']}")
        print(line)
        problems: List[str] = []
        if not within(result["health_min"], spec.get("health_min_range")):
            problems.append(f"santé min hors plage {spec.get('health_min_range')}")
        if not within(result["anomaly_median"], spec.get("anomaly_median_range")):
            problems.append(f"anomalie médiane hors plage {spec.get('anomaly_median_range')}")
        if not within(result["rul_min"], spec.get("rul_min_range")):
            problems.append(f"RUL min hors plage {spec.get('rul_min_range')}")
        if spec.get("fault_class") and spec["fault_class"] not in result["classes"]:
            problems.append(f"classe {spec['fault_class']} jamais diagnostiquée")
        if (spec.get("forbidden_classes")
                and any(f in spec["forbidden_classes"] for f in result["classes"])):
            problems.append(f"classe interdite diagnostiquée : "
                            f"{[f for f in result['classes'] if f in spec['forbidden_classes']]}")
        for problem in problems:
            print(f"   ⚠ {problem}")
        failures += len(problems)

    # --- Retour à l'état sain -------------------------------------------------
    recovery = expected.get("recovery", {})
    call("/api/simulator/clear-faults", {})
    result = sample("M1", float(recovery.get("window_s", 70)))
    print(f"récupération  M1 après effacement → santé min {result['health_min']:.1f} · "
          f"anomalie méd {result['anomaly_median']:.3f} · classes {result['classes']}")
    if result["health_min"] < float(recovery.get("health_min", 60.0)):
        print(f"   ⚠ santé de retour sous le seuil attendu ({recovery.get('health_min')})")
        failures += 1
    if result["anomaly_median"] > float(recovery.get("anomaly_median_max", 0.30)):
        print(f"   ⚠ anomalie de retour au-dessus du seuil attendu "
              f"({recovery.get('anomaly_median_max')})")
        failures += 1

    print(f"\n[drill] {'CONFORME' if failures == 0 else f'{failures} ÉCART(S) À CORRIGER'}")
    return 0 if failures == 0 else 1


# =============================================================================
#  Point d'entrée
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Amorçage et vérification de la base Conveyor AI Ops à partir de data/seed.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exemples :\n"
               "  python scripts/seed_db.py --check\n"
               "  python scripts/seed_db.py --apply --reset --history-hours 24\n"
               "  python scripts/seed_db.py --apply --api http://localhost:8000 --token $JWT\n"
               "  python scripts/seed_db.py --drill        # validation dynamique des défauts\n",
    )
    parser.add_argument("--file", default=str(DEFAULT_SEED_FILE), help="chemin du fichier de description du banc")
    parser.add_argument("--apply", action="store_true", help="écrit la description du banc dans la base")
    parser.add_argument("--check", action="store_true", help="vérifie l'installation par rapport aux valeurs de référence")
    parser.add_argument("--reset", action="store_true", help="efface les données de démonstration avant application")
    parser.add_argument("--history-hours", type=int, default=0,
                        help="régénère un historique de démonstration de N heures")
    parser.add_argument("--api", default=None, help="URL du backend pour appliquer/vérifier via REST")
    parser.add_argument("--token", default=None, help="jeton JWT (pour les écritures via l'API)")
    parser.add_argument("--dry-run", action="store_true", help="n'écrit rien, affiche seulement les opérations")
    parser.add_argument("--drill", action="store_true",
                        help="joue les défauts de référence et vérifie la réaction du système")
    parser.add_argument("--drill-window", type=float, default=70.0,
                        help="durée d'observation après injection d'un défaut (s)")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    seed = load_seed(Path(args.file))
    started = time.time()

    if not args.apply and not args.check and not args.drill:
        # Sans action explicite : afficher la fiche du banc (utile en soutenance)
        print(f"Banc « {seed['line']['name']} »")
        print(f"  Moteurs          : {', '.join(m['label'] for m in seed['motors'])}")
        print(f"  Passerelle       : {seed['gateway']['device_id']} ({seed['gateway']['ip']})")
        print(f"  Capteurs         : {len(seed['gateway']['sensors'])}")
        print(f"  Défauts jouables : {len(seed['fault_catalog'])}")
        print(f"  Scénario         : {seed['demo_scenario']['name']} "
              f"({len(seed['demo_scenario']['steps'])} jalons / {seed['demo_scenario']['duration_s']} s)")
        print(f"  Modèle actif     : {seed['models'][0]['name']} {seed['models'][0]['version']} "
              f"(acc {seed['models'][0]['accuracy']:.4f})")
        print("\nActions possibles : --apply (écrire en base), --check (vérifier), --drill (défauts), "
              "--reset (repartir d'une base propre), --dry-run (simulation).")
        return 0

    exit_code = 0
    if args.apply:
        if args.api:
            stats = apply_via_api(seed, args.api, args.token)
            print(f"[seed] Application via API {args.api} : {stats}")
        else:
            stats = apply_to_database(seed, dry_run=args.dry_run,
                                      history_hours=args.history_hours, reset=args.reset)
            print(f"[seed] Application en base : {stats}")
    if args.check:
        exit_code = check_installation(seed, api=args.api) or exit_code
    if args.drill:
        exit_code = run_fault_drills(seed, api=args.api, window_s=args.drill_window) or exit_code

    print(f"[seed] Terminé en {time.time() - started:.1f} s")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
