"""API console de démonstration : contrôle du banc, injection de défauts, scénario."""
from __future__ import annotations

import time
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, delete

from ..config import settings
from ..db import get_session
from ..models import Alert, HealthSnapshot, Intervention, InferenceRecord, TelemetryRecord, WorkOrder
from ..realtime import hub
from ..schemas import DemoScenario, FaultInjection, SimulatorCommand
from ..services.common import logger
from ..services.mqtt_client import mqtt_bridge
from ..services.simulator import simulator

router = APIRouter(prefix="/api/simulator", tags=["simulator"])

FAULTS: List[Dict[str, str]] = [
    {"id": "BEARING_FAULT", "label": "Roulement usé", "icon": "circle-dot",
     "description": "Chocs périodiques (BPFO) + hausse de kurtosis et de température roulement"},
    {"id": "UNBALANCE", "label": "Déséquilibre", "icon": "scale",
     "description": "Amplification de l'harmonique 1x, vibration globale en hausse"},
    {"id": "MISALIGNMENT", "label": "Désalignement", "icon": "git-compare",
     "description": "Harmonique 2x dominante + élévation du courant"},
    {"id": "BELT_SLIP", "label": "Glissement de bande", "icon": "waves",
     "description": "Chute de vitesse, instabilité de rotation, écart vitesse moteur/bande"},
    {"id": "OVERLOAD", "label": "Surcharge", "icon": "weight",
     "description": "Courant et puissance élevés, vitesse qui décroche, échauffement moteur"},
    {"id": "OVERHEATING", "label": "Surchauffe", "icon": "thermometer",
     "description": "Dérive thermique du roulement et du corps moteur"},
    {"id": "SENSOR_FAULT", "label": "Perte de capteur", "icon": "unplug",
     "description": "Capteur figé : valeurs constantes, incohérence détectée"},
    {"id": "MOTOR_STALL", "label": "Blocage moteur", "icon": "octagon-alert",
     "description": "Vitesse nulle avec courant de blocage : arrêt d'urgence"},
]


@router.get("/faults", summary="Catalogue des défauts injectables")
def fault_catalog() -> List[Dict[str, str]]:
    return FAULTS


@router.get("/state", summary="État complet du simulateur")
def state() -> Dict[str, Any]:
    data = simulator.state()
    data["mqtt"] = mqtt_bridge.stats()
    data["available"] = settings.is_simulation
    data["fault_catalog"] = FAULTS
    return data


@router.post("/control", summary="Contrôles du banc (start/stop, vitesse, charge)")
async def control(payload: SimulatorCommand) -> Dict[str, Any]:
    """Applique la consigne au banc.

    * MODE=SIMULATION : la physique interne est pilotée **et** la consigne est publiée
      en MQTT (même flux que le matériel, utile pour tester un ESP32 réel en parallèle).
    * MODE=HARDWARE : aucune simulation interne — la consigne part uniquement sur
      `conveyor/cmd/all` et c'est le banc réel (ou `scripts/esp32_simulator.py`) qui l'applique.
    """
    command = {
        "running": payload.running, "speed_rpm": payload.speed_rpm, "load_kg": payload.load_kg,
        "parcels_per_min": payload.parcels_per_min, "ramp_s": payload.ramp_s,
    }
    forwarded = mqtt_bridge.send_command("all", "set_controls", command)
    if not settings.is_simulation:
        logger.info("MODE=HARDWARE — consigne relayée en MQTT : %s", command)
        return {
            "mode": "HARDWARE", "available": False, "forwarded": forwarded, "command": command,
            "detail": "Consigne publiée sur conveyor/cmd/all — appliquée par l'ESP32 (ou l'ESP32 simulé).",
            "motors": simulator.state().get("motors", {}),
        }
    state_data = await simulator.set_controls(
        running=payload.running, speed_rpm=payload.speed_rpm, load_kg=payload.load_kg,
        parcels_per_min=payload.parcels_per_min, ramp_s=payload.ramp_s,
    )
    logger.info("Contrôle simulateur : %s", command)
    return {**state_data, "forwarded": forwarded}


@router.post("/inject-fault", summary="Injecter un défaut sur un moteur")
async def inject_fault(payload: FaultInjection) -> Dict[str, Any]:
    """Injecte un défaut, en simulation interne ou à distance via MQTT.

    En MODE=HARDWARE la commande `inject_fault` est publiée sur `conveyor/cmd/<moteur>` :
    elle est interprétée par `scripts/esp32_simulator.py` (banc logiciel) ou par le
    firmware de test chargé sur l'ESP32. La chaîne de mesure, elle, reste réelle.
    """
    parameters = {"fault": payload.fault, "intensity": payload.intensity,
                  "degradation_rate": payload.degradation_rate,
                  "auto_clear_after_s": payload.auto_clear_after_s}
    forwarded = mqtt_bridge.send_command(payload.motor_id, "inject_fault", parameters)
    if not settings.is_simulation:
        logger.info("MODE=HARDWARE — injection relayée en MQTT : %s %s", payload.motor_id, parameters)
        return {
            "mode": "HARDWARE", "available": False, "forwarded": forwarded,
            "motor_id": payload.motor_id, "fault": payload.fault, "intensity": payload.intensity,
            "detail": (f"Commande inject_fault publiée sur conveyor/cmd/{payload.motor_id}. "
                       "Le device applique le défaut puis la télémétrie revient par MQTT."),
        }
    try:
        state_data = await simulator.inject_fault(
            payload.motor_id, payload.fault, payload.intensity,
            payload.degradation_rate, payload.auto_clear_after_s,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    return {**state_data, "forwarded": forwarded}


@router.post("/clear-faults", summary="Effacer tous les défauts (retour à l'état sain)")
async def clear_faults() -> Dict[str, Any]:
    forwarded = mqtt_bridge.send_command("all", "clear_fault", {})
    if not settings.is_simulation:
        logger.info("MODE=HARDWARE — remise à zéro relayée en MQTT (forwarded=%s)", forwarded)
        return {"mode": "HARDWARE", "available": False, "forwarded": forwarded,
                "detail": "Commande clear_fault publiée sur conveyor/cmd/all."}
    return {**await simulator.clear_all_faults(), "forwarded": forwarded}


@router.post("/scenario", summary="Lancer le scénario de démonstration")
async def run_scenario(payload: DemoScenario) -> Dict[str, Any]:
    if not settings.is_simulation:
        # Le scénario est rejoué par la passerelle (script ou firmware de test)
        forwarded = mqtt_bridge.send_command("all", "run_scenario", {"name": payload.name})
        logger.info("MODE=HARDWARE — scénario %s relayé en MQTT (forwarded=%s)", payload.name, forwarded)
        return {"mode": "HARDWARE", "available": False, "forwarded": forwarded,
                "scenario": payload.name, "steps": simulator.scenario_steps(payload.name),
                "detail": "Scénario confié au banc via conveyor/cmd/all (commande run_scenario)."}
    return await simulator.run_demo_scenario(payload.name)


@router.get("/scenario", summary="État du scénario en cours + timeline des jalons")
def scenario_state(name: str = "demo-3min") -> Dict[str, Any]:
    """Renvoie le scénario en cours et la timeline complète des jalons.

    L'interface s'en sert pour afficher la frise de progression aussi bien en
    MODE=SIMULATION (exécution interne) qu'en MODE=HARDWARE (exécution par le banc).
    """
    steps = simulator.scenario_steps(name)
    return {
        "scenario": simulator.active_scenario,
        "name": name,
        "steps": steps,
        "duration_s": max([int(step.get("t", 0)) for step in steps], default=0) + 2,
        "executor": "backend" if settings.is_simulation else "device",
    }


@router.post("/stop-scenario", summary="Interrompre le scénario en cours")
async def stop_scenario() -> Dict[str, Any]:
    if not settings.is_simulation:
        forwarded = mqtt_bridge.send_command("all", "stop_scenario", {})
        return {"mode": "HARDWARE", "stopped": forwarded.get("published", False), "forwarded": forwarded}
    if simulator._scenario_task and not simulator._scenario_task.done():  # noqa: SLF001
        simulator._scenario_task.cancel()  # noqa: SLF001
        simulator.active_scenario = None
        await hub.broadcast({"type": "scenario", "data": None})
        return {"stopped": True}
    return {"stopped": False}


@router.post("/fault-sequence", summary="Injecter une séquence de défauts (rotation automatique)")
async def fault_sequence(motor_id: str = "M1", step_s: float = 20.0) -> Dict[str, Any]:
    """Démonstration en boucle : chaque défaut est injecté `step_s` secondes."""
    if not settings.is_simulation:
        # Relais vers le banc : la passerelle fait tourner la rotation de défauts
        forwarded = mqtt_bridge.send_command("all", "fault_sequence",
                                             {"motor_id": motor_id, "step_s": step_s})
        return {"mode": "HARDWARE", "available": False, "forwarded": forwarded,
                "motor_id": motor_id, "step_s": step_s, "steps": len(FAULTS),
                "detail": "Rotation de défauts confiée au banc via conveyor/cmd/all."}
    import asyncio

    async def _sequence() -> None:
        try:
            for fault in [f["id"] for f in FAULTS if f["id"] not in ("NONE",)]:
                await simulator.inject_fault(motor_id, fault, 70.0, 1.0)
                await asyncio.sleep(step_s)
            await simulator.clear_all_faults()
        except asyncio.CancelledError:
            raise

    asyncio.create_task(_sequence())
    return {"status": "sequence_started", "motor_id": motor_id, "step_s": step_s,
            "steps": len(FAULTS)}


@router.post("/reset-data", summary="Vider les données de démonstration (télémétrie, alertes…)")
def reset_data(keep_rules: bool = True, session: Session = Depends(get_session)) -> Dict[str, Any]:
    models = [TelemetryRecord, InferenceRecord, HealthSnapshot, Alert, WorkOrder, Intervention]
    for model in models:
        session.exec(delete(model))
    if not keep_rules:
        from ..models import AlertRule
        session.exec(delete(AlertRule))
    session.commit()
    for motor_id in ("M1", "M2"):
        hub.telemetry[motor_id]._data.clear()      # noqa: SLF001
        hub.features[motor_id]._data.clear()       # noqa: SLF001
        hub.inference[motor_id]._data.clear()      # noqa: SLF001
        hub.edge_inference[motor_id]._data.clear()  # noqa: SLF001
    hub.alerts._data.clear()                       # noqa: SLF001
    logger.warning("Données de démonstration réinitialisées (règles conservées : %s)", keep_rules)
    return {"reset": True, "at": int(time.time() * 1000)}
