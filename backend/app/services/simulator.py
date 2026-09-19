"""Simulateur temps réel du banc d'essai (mode SIMULATION / HYBRID).

Boucle asyncio qui fait tourner le modèle physique des deux moteurs pas à pas,
produit les payloads de télémétrie conformes au protocole MQTT, publie sur le
hub temps réel (WebSocket) et — en option — sur un vrai broker MQTT
(`SIM_PUBLISH_MQTT=true`) pour valider la chaîne matérielle.

Il gère aussi la console de démonstration : start/stop, vitesse, charge,
injection de défauts, dégradation progressive et scénario « démo 3 minutes ».
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from ..config import settings
from ..realtime import hub
from .common import logger
from .ingestion import ingest_status, ingest_telemetry
from .ml_service import ml_service

# ==========================================================================
#  Timeline du scénario « démo 3 minutes » (soutenance)
#  Réutilisée par `_demo_script()` et exposée à l'API pour l'affichage de la frise.
# ==========================================================================
DEMO_SCENARIO_STEPS: List[Dict[str, Any]] = [
    {"t": 0, "action": "reset", "label": "Remise à zéro : ligne nominale"},
    {"t": 8, "action": "speed", "value": 150, "label": "Montée en cadence (150 rpm)"},
    {"t": 22, "action": "fault", "motor": "M1", "fault": "UNBALANCE", "intensity": 45,
     "label": "M1 : déséquilibre léger (45 %)"},
    {"t": 40, "action": "fault", "motor": "M1", "fault": "UNBALANCE", "intensity": 85,
     "label": "M1 : déséquilibre aggravé (85 %)"},
    {"t": 60, "action": "fault", "motor": "M2", "fault": "BEARING_FAULT", "intensity": 55,
     "label": "M2 : défaut de roulement (55 %)"},
    {"t": 85, "action": "load", "value": 1.6, "label": "Charge lourde (1,6 kg) — surcharge"},
    {"t": 105, "action": "fault", "motor": "M2", "fault": "OVERHEATING", "intensity": 70,
     "label": "M2 : surchauffe roulement (70 %)"},
    {"t": 125, "action": "fault", "motor": "M1", "fault": "BELT_SLIP", "intensity": 60,
     "label": "M1 : glissement de bande (60 %)"},
    {"t": 145, "action": "fault", "motor": "M2", "fault": "MOTOR_STALL", "intensity": 100,
     "label": "M2 : blocage moteur (arrêt d'urgence)"},
    {"t": 158, "action": "stop", "label": "Arrêt de ligne (sécurité)"},
    {"t": 165, "action": "reset", "label": "Maintenance : remise en état nominale"},
    {"t": 172, "action": "start", "label": "Redémarrage — retour à la normale"},
]


try:
    from ml.physics import MotorPhysics
    from ml.synthetic import telemetry_from_physics
except Exception as exc:  # pragma: no cover
    MotorPhysics = None  # type: ignore
    telemetry_from_physics = None  # type: ignore
    logger.error("Simulateur indisponible : %s", exc)


class ConveyorSimulator:
    """Banc d'essai virtuel : 2 moteurs + bande + charge."""

    def __init__(self) -> None:
        self.motors: Dict[str, Any] = {}
        self.running: bool = True
        self.target_rpm: float = 120.0
        self.load_kg: float = 0.6
        self.parcels_per_min: float = 12.0
        self.ramp_s: float = 2.0
        self.tick_hz: float = max(1.0, settings.sim_hz)
        self._task: Optional[asyncio.Task] = None
        self._scenario_task: Optional[asyncio.Task] = None
        self.active_scenario: Optional[Dict[str, Any]] = None
        self.started_at: float = time.time()
        self.last_tick: float = 0.0
        self.tick_count: int = 0
        self.mqtt_publisher = None  # injecté par le service MQTT
        self.fault_log: List[Dict[str, Any]] = []
        self._init_motors()

    # ------------------------------------------------------------------
    def _init_motors(self) -> None:
        if MotorPhysics is None:
            return
        self.motors = {
            "M1": MotorPhysics(motor_id="M1", target_rpm=self.target_rpm, load_kg=self.load_kg,
                               seed=101, ambient_c=25.5),
            "M2": MotorPhysics(motor_id="M2", target_rpm=self.target_rpm * 0.995, load_kg=self.load_kg,
                               seed=202, ambient_c=25.8),
        }
        # Démarrage déjà au régime thermique établi (le banc tourne depuis un moment)
        for index, motor in enumerate(self.motors.values()):
            motor.rpm = motor.target_rpm
            motor.uptime_s = 3600.0 * (1 + index)
            motor.settle_thermal(current_rms=0.73, seconds=900.0, dt=15.0)

    # ------------------------------------------------------------------
    #  Cycle de vie
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="conveyor-simulator")
        logger.info("Simulateur démarré (%s Hz, M1+M2)", self.tick_hz)

    async def stop(self) -> None:
        for task in (self._scenario_task, self._task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._task = None
        self._scenario_task = None
        logger.info("Simulateur arrêté")

    @property
    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    # ------------------------------------------------------------------
    #  Boucle principale
    # ------------------------------------------------------------------
    async def _run(self) -> None:
        dt = 1.0 / self.tick_hz
        now = time.time()
        try:
            while True:
                loop_start = time.time()
                now = loop_start
                # Rampes douces des consignes (comportement d'entraînement réel)
                for motor_id, phys in self.motors.items():
                    phys.load_kg += (self.load_kg - phys.load_kg) * min(1.0, dt / max(self.ramp_s, 0.1))
                    phys.set_running(self.running)
                # Consigne M2 très légèrement différente : écart de synchronisation réaliste
                self.motors["M1"].target_rpm = self.target_rpm
                self.motors["M2"].target_rpm = self.target_rpm * 0.995

                for motor_id, phys in self.motors.items():
                    # La consigne publiée est celle réellement visée par CE moteur : publier la
                    # consigne de M1 pour M2 (écart de 0,5 %) faisait apparaître un
                    # « speed_error » fantôme que le détecteur lisait comme un défaut.
                    telemetry = telemetry_from_physics(phys, now - self.started_at, dt, phys.target_rpm)
                    telemetry["ts"] = int(now * 1000)
                    telemetry["_wear"] = round(phys.wear, 4)
                    await ingest_telemetry(telemetry, source="sim")
                    await self._publish_mqtt(f"conveyor/{motor_id.lower()}/telemetry", telemetry)

                    # L'edge (TinyML) est simulé : décision rapide basée sur la physique.
                    # Cadence 1 Hz comme dans le cahier des charges (le microcontrôleur publie
                    # sa décision sur .../edge_inference), et la trame est enregistrée comme
                    # une VRAIE inférence embarquée : sans cela, la comparaison « edge vs
                    # serveur » de la fiche moteur restait à zéro échantillon en simulation.
                    if self.tick_count % max(1, int(self.tick_hz)) == 0:
                        edge = self._edge_inference(motor_id, phys, telemetry)
                        comparison = ml_service.register_edge_inference(edge)
                        # Diffusion WebSocket identique au chemin MQTT : la fiche moteur
                        # affiche la comparaison embarqué/serveur même sans matériel.
                        await hub.broadcast({"type": "edge_inference", "motor_id": motor_id,
                                             "data": edge, "comparison": comparison})
                        await self._publish_mqtt(f"conveyor/{motor_id.lower()}/edge_inference", edge)

                # --- Status global (5 Hz suffit) ---
                if self.tick_count % max(1, int(self.tick_hz / 5)) == 0:
                    status = {
                        "device_id": "esp32-conveyor-01", "ts": int(now * 1000),
                        "running": self.running,
                        "target_speed_rpm": self.target_rpm,
                        "belt_mm_s": self._belt_speed(),
                        "load_kg": round(self.load_kg, 3),
                        "target_parcels_per_min": self.parcels_per_min,
                        "mode": settings.mode,
                    }
                    await ingest_status(status)
                    await self._publish_mqtt("conveyor/status", status)

                # --- Heartbeat device (1 Hz) ---
                if self.tick_count % max(1, int(self.tick_hz)) == 0:
                    from .ingestion import ingest_heartbeat
                    hb = {
                        "device_id": "esp32-conveyor-01", "ts": int(now * 1000),
                        "uptime_s": round(now - self.started_at, 1),
                        "rssi_dbm": -58 + int(4 * ((-1) ** self.tick_count)),
                        "firmware": "1.0.0-sim", "ip": "192.168.1.42", "free_heap_kb": 143.5,
                    }
                    await ingest_heartbeat(hb)
                    await self._publish_mqtt("conveyor/esp32-conveyor-01/heartbeat", hb)

                self.tick_count += 1
                self.last_tick = time.time()
                elapsed = time.time() - loop_start
                await asyncio.sleep(max(0.0, dt - elapsed))
        except asyncio.CancelledError:  # arrêt propre
            raise
        except Exception as exc:  # pragma: no cover
            logger.exception("Simulateur interrompu : %s", exc)

    def _belt_speed(self) -> float:
        if not self.motors:
            return self.target_rpm / 60.0 * 42.0
        avg_rpm = sum(m.rpm for m in self.motors.values()) / 2
        return round(avg_rpm / 60.0 * 42.0, 2)

    def _edge_inference(self, motor_id: str, phys: Any, telemetry: Dict[str, Any]) -> Dict[str, Any]:
        """TinyML simulé : règles embarquées sobres (ce que ferait l'ESP32)."""
        vib = telemetry["vibration"]
        cur = telemetry["current"]
        temp = telemetry["temperature"]
        score = 0.0
        score += min(1.0, max(0.0, (vib["rms"] - 0.12) / 0.45)) * 0.4
        score += min(1.0, max(0.0, (vib["kurtosis"] - 3.2) / 5.0)) * 0.25
        score += min(1.0, max(0.0, (temp["bearing_c"] - 46) / 22.0)) * 0.2
        score += min(1.0, max(0.0, (cur["rms_a"] - 0.9) / 0.9)) * 0.15
        score = round(min(1.0, score), 3)
        fault = phys.fault.fault if phys.fault.active else "HEALTHY"
        return {
            "device_id": "esp32-conveyor-01", "motor_id": motor_id, "ts": telemetry["ts"],
            "health_index": round(max(0.0, 100.0 - 70 * score), 1),
            "anomaly_score": score,
            "is_anomaly": score > 0.55,
            "fault_class": "HEALTHY" if score <= 0.35 else fault,
            "model_version": "tiny-mlp-v1.0.0-int8",
            "inference_ms": round(2.1 + 1.4 * score, 2),
        }

    async def _publish_mqtt(self, topic: str, payload: Dict[str, Any]) -> None:
        if self.mqtt_publisher and settings.sim_publish_mqtt:
            try:
                self.mqtt_publisher.publish_json(topic, payload)
            except Exception as exc:  # pragma: no cover
                logger.debug("Publication MQTT simulée échouée : %s", exc)

    # ------------------------------------------------------------------
    #  Commandes (API /simulator)
    # ------------------------------------------------------------------
    async def set_controls(self, *, running: Optional[bool] = None, speed_rpm: Optional[float] = None,
                           load_kg: Optional[float] = None, parcels_per_min: Optional[float] = None,
                           ramp_s: Optional[float] = None) -> Dict[str, Any]:
        if running is not None:
            self.running = bool(running)
            for phys in self.motors.values():
                phys.set_running(self.running)
        if speed_rpm is not None:
            self.target_rpm = float(max(0.0, min(300.0, speed_rpm)))
        if load_kg is not None:
            self.load_kg = float(max(0.0, min(3.0, load_kg)))
        if parcels_per_min is not None:
            self.parcels_per_min = float(max(0.0, min(60.0, parcels_per_min)))
        if ramp_s is not None:
            self.ramp_s = float(max(0.05, min(30.0, ramp_s)))
        hub.status.update({
            "running": self.running, "speed_rpm": self.target_rpm,
            "load_kg": round(self.load_kg, 3), "parcels_per_min": self.parcels_per_min,
            "belt_mm_s": self._belt_speed(),
            "ts": int(time.time() * 1000),
        })
        await hub.broadcast({"type": "status", "data": hub.status})
        return self.state()

    async def inject_fault(self, motor_id: str, fault: str, intensity: float = 50.0,
                           degradation_rate: float = 1.0,
                           auto_clear_after_s: Optional[float] = None) -> Dict[str, Any]:
        phys = self.motors.get(motor_id)
        if not phys:
            raise KeyError(f"Moteur inconnu : {motor_id}")
        now = time.time() - self.started_at
        if fault == "NONE":
            phys.clear_fault()
        else:
            phys.inject_fault(fault, intensity, degradation_rate, auto_clear_after_s, now)
            # Un défaut sérieux impacte aussi l'autre moteur (couple transmis par la bande)
            other = "M2" if motor_id == "M1" else "M1"
            if fault in ("OVERLOAD", "BELT_SLIP", "MOTOR_STALL"):
                self.motors[other].load_kg = min(3.0, self.load_kg * 1.25)
        self.fault_log.append({
            "ts": int(time.time() * 1000), "motor_id": motor_id, "fault": fault,
            "intensity": intensity, "degradation_rate": degradation_rate,
        })
        self.fault_log[:] = self.fault_log[-100:]
        await hub.broadcast({"type": "fault_injected", "data": self.fault_log[-1]})
        logger.info("Défaut injecté : %s sur %s (%s %%)", fault, motor_id, intensity)
        return self.state()

    async def clear_all_faults(self) -> Dict[str, Any]:
        for phys in self.motors.values():
            phys.clear_fault()
        await hub.broadcast({"type": "fault_cleared", "data": {"ts": int(time.time() * 1000)}})
        return self.state()

    # ------------------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        return {
            "mode": settings.mode,
            "running": self.running,
            "target_rpm": self.target_rpm,
            "load_kg": round(self.load_kg, 3),
            "parcels_per_min": self.parcels_per_min,
            "belt_mm_s": self._belt_speed(),
            "tick_hz": self.tick_hz,
            "is_running": self.is_running,
            "ticks": self.tick_count,
            "uptime_s": round(time.time() - self.started_at, 1),
            "motors": {
                m: {
                    "fault": phys.fault.fault,
                    "intensity": round(phys.fault.intensity * 100, 1),
                    "effective_intensity": round(phys._effective_intensity(time.time() - self.started_at) * 100, 1),
                    "degradation_rate": phys.fault.degradation_rate,
                    "rpm": round(phys.rpm, 2),
                    "wear": round(phys.wear, 4),
                    "temp_motor": round(phys.temp_motor, 2),
                    "temp_bearing": round(phys.temp_bearing, 2),
                    "uptime_s": round(phys.uptime_s, 1),
                }
                for m, phys in self.motors.items()
            },
            "fault_log": self.fault_log[-10:],
            "scenario": self.active_scenario,
        }

    # ------------------------------------------------------------------
    #  Scénario de soutenance « démo 3 minutes »
    # ------------------------------------------------------------------
    async def run_demo_scenario(self, name: str = "demo-3min") -> Dict[str, Any]:
        if self._scenario_task and not self._scenario_task.done():
            self._scenario_task.cancel()
        self._scenario_task = asyncio.create_task(self._demo_script(name), name="demo-scenario")
        return {"scenario": name, "status": "started"}

    def scenario_steps(self, name: str = "demo-3min") -> List[Dict[str, Any]]:
        """Timeline du scénario de démonstration.

        Exposée par l'API (`GET /api/simulator/scenario`) afin que l'interface puisse
        afficher la frise des jalons — y compris en MODE=HARDWARE, où le scénario est
        exécuté par le banc réel et non par le simulateur interne.
        """
        if name not in ("demo-3min", "demo-180", ""):
            return []
        return [dict(step) for step in DEMO_SCENARIO_STEPS]

    async def _demo_script(self, name: str) -> None:
        """Enchaînement scénarisé pour la soutenance (174 s, mêmes jalons que le firmware)."""
        steps: List[Dict[str, Any]] = self.scenario_steps(name or "demo-3min")
        start = time.time()
        self.active_scenario = {"name": name, "started_at": int(start * 1000), "steps": len(steps),
                                "duration_s": 174, "current_step": -1, "label": "Démarrage…"}
        await hub.broadcast({"type": "scenario", "data": self.active_scenario})
        try:
            for index, step in enumerate(steps):
                delay = start + step["t"] - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                self.active_scenario.update({"current_step": index, "label": step.get("label", ""),
                                             "elapsed_s": round(time.time() - start, 1)})
                await hub.broadcast({"type": "scenario", "data": self.active_scenario})
                action = step["action"]
                if action == "reset":
                    await self.clear_all_faults()
                    await self.set_controls(load_kg=0.6, speed_rpm=120)
                elif action == "fault":
                    await self.inject_fault(step["motor"], step["fault"], step.get("intensity", 60),
                                            degradation_rate=1.2)
                elif action == "speed":
                    await self.set_controls(speed_rpm=step["value"])
                elif action == "load":
                    await self.set_controls(load_kg=step["value"])
                elif action == "stop":
                    await self.set_controls(running=False)
                elif action == "start":
                    await self.set_controls(running=True, speed_rpm=120, load_kg=0.6)
                logger.info("SCÉNARIO [%d/%d] %s", index + 1, len(steps), step.get("label", ""))
            self.active_scenario.update({"current_step": len(steps), "label": "Scénario terminé",
                                         "done": True})
            await hub.broadcast({"type": "scenario", "data": self.active_scenario})
        except asyncio.CancelledError:
            self.active_scenario = None
            raise
        except Exception as exc:  # pragma: no cover
            logger.exception("Scénario interrompu : %s", exc)


simulator = ConveyorSimulator()
