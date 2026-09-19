#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simulateur de passerelle ESP32 — émetteur MQTT autonome
=======================================================

Ce script reproduit **exactement** le comportement de la carte réelle
(`firmware/esp32_conveyor.ino`) sans matériel : il publie la télémétrie des deux
moteurs pas à pas sur le broker MQTT, calcule les caractéristiques embarquées,
exécute une inférence « TinyML » et réagit aux commandes descendantes.

Il est utile pour :

* valider la chaîne MQTT de bout en bout (`docker compose --profile edge up`),
* démontrer le mode `MODE=HARDWARE` sans banc physique,
* injecter des défauts à distance pendant une soutenance,
* tester la résilience (coupures réseau, perte de capteur, blocage moteur).

Topics publiés (contrat identique au firmware)
---------------------------------------------
    conveyor/m1/telemetry        JSON complet     8 Hz par défaut
    conveyor/m2/telemetry        JSON complet     8 Hz
    conveyor/m1/features         21 caractéristiques + signature spectrale  1 Hz
    conveyor/m1/edge_inference   diagnostic embarqué (TinyML)              1 Hz
    conveyor/status              état de ligne (marche, vitesse, charge)   5 Hz
    conveyor/esp32-conveyor-01/heartbeat  supervision de connectivité       1 Hz

Topics écoutés
--------------
    conveyor/cmd/#      set_controls | inject_fault | clear_fault | reboot |
                        set_sampling | calibrate | ota_update | test_network
    conveyor/config/#   mise à jour de configuration (seuils, cadence…)

Exemples
--------
    # publication continue sur un broker local
    python scripts/esp32_simulator.py --host localhost

    # démonstration complète en 3 minutes (mêmes jalons que la console web)
    python scripts/esp32_simulator.py --scenario demo-3min

    # injecter un défaut depuis un autre poste, en parallèle du flux
    python scripts/esp32_simulator.py fault --motor M1 --fault UNBALANCE --intensity 85

    # arrêter / relancer la ligne
    python scripts/esp32_simulator.py control --stop
    python scripts/esp32_simulator.py control --speed 150 --load 1.2
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import signal
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- Import de la physique du projet (utilisée aussi par le backend) ---------
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - dépend de l'environnement d'exécution
    from ml.physics import MotorPhysics
    from ml.synthetic import telemetry_from_physics
    from ml.features import extract_from_telemetry
    from ml.model import FEATURE_LABELS  # noqa: F401  (documentation des contributions)
    PHYSICS_AVAILABLE = True
except Exception:  # pragma: no cover
    PHYSICS_AVAILABLE = False

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    print("[erreur] paho-mqtt est requis : pip install paho-mqtt", file=sys.stderr)
    raise SystemExit(2)

# paho-mqtt 2.x introduit une nouvelle API de rappels (VERSION2) : on l'utilise
# quand elle est disponible, sinon on retombe sur l'API historique 1.x.
try:  # pragma: no cover - dépend de la version installée
    from paho.mqtt.enums import CallbackAPIVersion

    _CALLBACK_API: Any = CallbackAPIVersion.VERSION2
except Exception:  # pragma: no cover
    _CALLBACK_API = None


def make_client(client_id: str, clean_session: bool = True) -> "mqtt.Client":
    """Fabrique un client MQTT compatible paho-mqtt 1.x et 2.x (sans avertissement)."""
    kwargs: Dict[str, Any] = {"client_id": client_id, "protocol": mqtt.MQTTv311}
    if _CALLBACK_API is not None:
        kwargs["callback_api_version"] = _CALLBACK_API
    return mqtt.Client(clean_session=clean_session, **kwargs)

DEVICE_ID = os.environ.get("DEVICE_ID", "esp32-conveyor-01")
FIRMWARE_VERSION = "1.0.0"
TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "conveyor")

MOTOR_LABELS = {"M1": "Moteur GAUCHE", "M2": "Moteur DROIT"}
BELT_MM_PER_REV = 42.0      # développement de la poulie d'entraînement (mm/tour)
STEPS_PER_REV = 200         # moteur pas à pas 1,8° (16 micro-pas côté driver)


# =============================================================================
#  Physique de repli (si le dossier backend/ml n'est pas disponible)
# =============================================================================
@dataclass
class SimplePhysics:
    """Modèle minimal : suffisant pour publier un flux crédible hors du projet."""

    motor_id: str = "M1"
    target_rpm: float = 120.0
    load_kg: float = 0.6
    ambient_c: float = 26.0
    rpm: float = 120.0
    temp_motor: float = 38.0
    temp_bearing: float = 40.0
    wear: float = 0.0
    fault: str = "NONE"
    intensity: float = 0.0
    running: bool = True
    uptime_s: float = 0.0
    rng: random.Random = field(default_factory=lambda: random.Random(7))

    def step(self, dt: float) -> Dict[str, Any]:
        fault = self.intensity / 100.0 if self.fault != "NONE" else 0.0
        target = self.target_rpm if self.running else 0.0
        if self.fault == "BELT_SLIP":
            target *= 1 - 0.35 * fault
        if self.fault == "OVERLOAD":
            target *= 1 - 0.30 * fault
        if self.fault == "MOTOR_STALL":
            target = 0.0
        self.rpm += (target - self.rpm) * min(1.0, dt / 0.35)
        if self.running:
            self.uptime_s += dt
            self.wear = min(1.0, self.wear + 1e-6 * dt)

        f_rot = max(self.rpm, 0.0) / 60.0
        unbalance = fault if self.fault == "UNBALANCE" else 0.0
        misalign = fault if self.fault == "MISALIGNMENT" else 0.0
        bearing = fault if self.fault == "BEARING_FAULT" else 0.0
        slip = fault if self.fault == "BELT_SLIP" else 0.0
        overload = fault if self.fault == "OVERLOAD" else 0.0
        overheat = fault if self.fault == "OVERHEATING" else 0.0

        a1 = 0.045 + 0.55 * unbalance + 0.06 * self.load_kg
        a2 = 0.020 + 0.42 * misalign + 0.25 * slip
        a3 = 0.010 + 0.10 * misalign + 0.05 * overload
        t = self.uptime_s
        vibration = (a1 * math.sin(2 * math.pi * f_rot * t + 0.3)
                     + a2 * math.sin(4 * math.pi * f_rot * t + 1.1)
                     + a3 * math.sin(6 * math.pi * f_rot * t + 0.7))
        noise = 0.012 + 0.02 * (self.rpm / 120.0) + 0.05 * misalign
        vibration += self.rng.gauss(0, noise) + 0.35 * bearing * self.rng.gauss(0, 0.25)
        rms = abs(vibration) * 0.72 + 0.02
        peak = rms * (3.0 + 3.0 * bearing)
        kurtosis = 3.0 + 6.0 * bearing

        current = (0.28 + 0.55 * self.load_kg + 0.12 * (self.rpm / 120.0)
                   + 0.9 * overload + 0.25 * misalign + 0.2 * bearing)
        current *= 1.9 if self.fault == "MOTOR_STALL" else 1.0
        current = 0.06 * current if not self.running else current
        current += self.rng.gauss(0, 0.03)

        target_motor = self.ambient_c + 12.0 * self.load_kg + 10.0 * (current / 1.2) + 34.0 * overheat
        self.temp_motor += (target_motor - self.temp_motor) * min(1.0, dt / 55.0)
        target_bearing = self.temp_motor + 2.2 + 11.0 * bearing + 3.0 * self.wear
        self.temp_bearing += (target_bearing - self.temp_bearing) * min(1.0, dt / 70.0)

        return {
            "vibration": {"ax": vibration * 0.62, "ay": vibration * 0.71, "az": 1.0 + abs(vibration) * 0.3,
                          "rms": rms, "peak": peak, "kurtosis": kurtosis, "crest_factor": peak / max(rms, 1e-6)},
            "fft_peaks": [{"freq": round(f_rot, 2), "amp": round(rms, 4)},
                          {"freq": round(2 * f_rot, 2), "amp": round(rms * (0.35 + misalign), 4)},
                          {"freq": round(3 * f_rot, 2), "amp": round(rms * 0.18, 4)},
                          {"freq": round(f_rot * 4.8, 2), "amp": round(rms * (0.12 + bearing), 4)}],
            "current": {"rms_a": current, "peak_a": current * 1.42, "power_w": current * 12.0},
            "temperature": {"motor_c": self.temp_motor, "bearing_c": self.temp_bearing, "ambient_c": self.ambient_c},
            "acoustic": {"spl_db": 52.0 + 20 * math.log10(max(rms, 1e-3)) + 12 * overload + 6 * slip,
                         "band_energy": [rms * 0.4, rms * 0.9, rms * 0.5, rms * 0.2]},
            "speed": {"rpm": self.rpm, "steps_per_s": self.rpm / 60.0 * STEPS_PER_REV,
                      "belt_mm_s": self.rpm / 60.0 * BELT_MM_PER_REV},
            "state": "RUNNING" if self.running else "STOPPED",
        }


# =============================================================================
#  Corps de la passerelle simulée
# =============================================================================
class GatewaySimulator:
    """Émule la carte ESP32 : physique, calcul embarqué, publication MQTT."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.hz = float(args.hz)
        self.dt = 1.0 / self.hz
        self.motors: Dict[str, Any] = {}
        self.rpm_windows: Dict[str, List[float]] = {"M1": [], "M2": []}
        self.running = True
        self.speed_rpm = float(args.speed)
        self.load_kg = float(args.load)
        self.parcels_per_min = float(args.parcels)
        self.started_at = time.time()
        self.tick = 0
        self.stop_requested = False
        self._scenario_stop = False
        for index, motor_id in enumerate([m.strip().upper() for m in args.motors.split(",") if m.strip()]):
            self.motors[motor_id] = self._build_physics(motor_id, seed=11 + index)

        self.client = make_client(f"{DEVICE_ID}-sim")
        if args.username:
            self.client.username_pw_set(args.username, args.password or None)
        if args.tls:
            self.client.tls_set(ca_certs=args.ca_cert or None, certfile=args.cert or None,
                                keyfile=args.key or None, tls_version=ssl.PROTOCOL_TLSv1_2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = lambda *_a, **_k: self._log(
            "déconnecté du broker (reconnexion automatique en cours)")

    # ------------------------------------------------------------------ outils
    def _log(self, message: str) -> None:
        if not self.args.quiet:
            print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def _build_physics(self, motor_id: str, seed: int) -> Any:
        if PHYSICS_AVAILABLE:
            physics = MotorPhysics(motor_id=motor_id, target_rpm=self.speed_rpm,
                                   load_kg=self.load_kg, seed=seed)
            physics.settle_thermal(current_rms=0.28 + 0.55 * self.load_kg)
            return physics
        return SimplePhysics(motor_id=motor_id, target_rpm=self.speed_rpm,
                             load_kg=self.load_kg, rng=random.Random(seed))

    def _fault_label(self, motor: Any) -> str:
        """Nom du défaut actif, quel que soit le type de physique utilisé."""
        fault = getattr(motor, "fault", "NONE")
        label = getattr(fault, "fault", fault)
        return str(label) if label else "NONE"

    def _publish(self, topic: str, payload: Dict[str, Any]) -> None:
        self.client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1, retain=False)

    # ------------------------------------------------------------- connexions
    def _on_connect(self, client: "mqtt.Client", _userdata: Any, _flags: Any,
                    reason_code: Any, *_rest: Any) -> None:
        rc = int(getattr(reason_code, "value", reason_code) or 0)
        if rc == 0:
            self._log(f"connecté au broker {self.args.host}:{self.args.port} (TLS={self.args.tls})")
            client.subscribe(f"{TOPIC_PREFIX}/cmd/#", qos=1)
            client.subscribe(f"{TOPIC_PREFIX}/config/#", qos=1)
            self._publish(f"{TOPIC_PREFIX}/status", {
                "device_id": DEVICE_ID, "ts": int(time.time() * 1000), "running": self.running,
                "target_speed_rpm": self.speed_rpm, "belt_mm_s": round(self.speed_rpm / 60.0 * BELT_MM_PER_REV, 2),
                "load_kg": self.load_kg, "mode": "HARDWARE-SIM", "firmware": FIRMWARE_VERSION,
            })
        else:
            self._log(f"échec de connexion MQTT (code {rc})")

    # --------------------------------------------------------------- commandes
    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        topic = message.topic
        try:
            payload = json.loads(message.payload.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._log(f"commande ignorée (JSON invalide) sur {topic}")
            return
        command = payload.get("command") or payload.get("action") or topic.split("/")[-1]
        target = topic.split("/")[2] if len(topic.split("/")) > 2 else "all"
        self._log(f"commande reçue : {command} → {target} {payload.get('params', payload)}")
        self._apply_command(command, payload.get("params", payload), target)

    def _apply_command(self, command: str, params: Dict[str, Any], target: str) -> None:
        if command in ("set_controls", "control"):
            if "running" in params:
                self.running = bool(params["running"])
                for motor in self.motors.values():
                    motor.set_running(self.running) if hasattr(motor, "set_running") else None
            if params.get("speed_rpm") is not None:
                self.speed_rpm = float(params["speed_rpm"])
                self.motors["M1"].target_rpm = self.speed_rpm
                if "M2" in self.motors:
                    self.motors["M2"].target_rpm = self.speed_rpm * 0.995
            if params.get("load_kg") is not None:
                self.load_kg = float(params["load_kg"])
                for motor in self.motors.values():
                    motor.load_kg = self.load_kg
            if params.get("parcels_per_min") is not None:
                self.parcels_per_min = float(params["parcels_per_min"])
        elif command in ("inject_fault", "fault"):
            motor_id = str(params.get("motor_id") or target).upper()
            if motor_id not in self.motors:
                self._log(f"moteur inconnu : {motor_id}")
                return
            fault = str(params.get("fault", "NONE")).upper()
            intensity = float(params.get("intensity", 60))
            degradation = float(params.get("degradation_rate", 1.0))
            motor = self.motors[motor_id]
            if hasattr(motor, "inject_fault"):
                # `now` : horodatage interne de la physique (secondes depuis le démarrage)
                motor.inject_fault(fault, intensity, degradation_rate=degradation,
                                   auto_clear_after_s=params.get("auto_clear_after_s"),
                                   now=time.time() - self.started_at)
            else:
                motor.fault, motor.intensity = fault, intensity
            self._log(f"défaut {fault} à {intensity:.0f} % appliqué sur {motor_id}")
        elif command in ("clear_fault", "clear"):
            for motor in self.motors.values():
                motor.clear_fault() if hasattr(motor, "clear_fault") else setattr(motor, "fault", "NONE")
            self._log("défauts effacés")
        elif command == "set_sampling":
            self.hz = max(1.0, min(50.0, float(params.get("hz", self.hz))))
            self.dt = 1.0 / self.hz
            self._log(f"cadence d'échantillonnage : {self.hz} Hz")
        elif command in ("calibrate", "recalibrate"):
            self._log("calibration exécutée (offset accéléromètre + zéro courant)")
        elif command == "reboot":
            self._log("redémarrage simulé : réinitialisation de la physique")
            self.motors = {mid: self._build_physics(mid, seed=11 + i)
                           for i, mid in enumerate(self.motors)}
        elif command == "ota_update":
            self._log(f"OTA simulé vers la version {params.get('version', '1.1.0')}")
        elif command in ("run_scenario", "scenario"):
            # Déclenché par POST /api/simulator/scenario : le scénario est rejoué
            # par la passerelle (thread séparé pour ne pas bloquer la publication).
            import threading

            name = str(params.get("name") or "demo-3min")
            threading.Thread(target=self.run_scenario, args=(name,), daemon=True,
                             name="scenario").start()
        elif command == "stop_scenario":
            self._scenario_stop = True
            self._apply_command("clear_fault", {}, "all")
            self._log("scénario interrompu (retour à l'état nominal)")
        elif command in ("fault_sequence", "run_fault_sequence"):
            # Rotation automatique de tous les défauts du catalogue
            import threading

            motor_id = str(params.get("motor_id") or "M1").upper()
            step_s = float(params.get("step_s", 20))
            threading.Thread(target=self._run_fault_sequence, args=(motor_id, step_s),
                             daemon=True, name="fault-sequence").start()
        elif command == "test_network":
            self._publish(f"{TOPIC_PREFIX}/api/network_test", {
                "device_id": DEVICE_ID, "ts": int(time.time() * 1000),
                "latency_ms": round(8 + random.random() * 6, 1), "rssi_dbm": -58,
                "packet_loss_pct": 0.0, "heap_kb": 176,
            })

    # ------------------------------------------------- rotation automatique
    def _run_fault_sequence(self, motor_id: str, step_s: float) -> None:
        """Injecte chaque défaut du catalogue l'un après l'autre (démonstration)."""
        catalog = ["BEARING_FAULT", "UNBALANCE", "MISALIGNMENT", "BELT_SLIP",
                   "OVERLOAD", "OVERHEATING", "SENSOR_FAULT", "MOTOR_STALL"]
        self._log(f"rotation de défauts démarrée sur {motor_id} ({step_s:g} s par défaut)")
        for fault in catalog:
            self._apply_command("inject_fault",
                                {"motor_id": motor_id, "fault": fault, "intensity": 70.0},
                                motor_id)
            time.sleep(step_s)
        self._apply_command("clear_fault", {}, "all")
        self._log("rotation de défauts terminée")

    # ------------------------------------------------------------------ boucle
    def _edge_inference(self, motor_id: str, telemetry: Dict[str, Any]) -> Dict[str, Any]:
        """Inférence « TinyML » embarquée (décision rapide, sans RUL détaillé)."""
        vibration = telemetry["vibration"]
        current = telemetry["current"]
        temperature = telemetry["temperature"]
        speed = telemetry["speed"]
        score = 0.0
        faults: Dict[str, float] = {}
        if vibration["kurtosis"] > 4.5:
            score += 0.35
            faults["BEARING_FAULT"] = 0.6
        if vibration["rms"] > 0.32:
            score += 0.3
            faults["UNBALANCE"] = faults.get("UNBALANCE", 0.0) + 0.55
        if abs(speed["rpm"] - self.speed_rpm) > 4.0:
            score += 0.25
            faults["BELT_SLIP"] = 0.7
        if current["rms_a"] > 1.35:
            score += 0.3
            faults["OVERLOAD"] = 0.75
        if temperature["bearing_c"] > 62:
            score += 0.35
            faults["OVERHEATING"] = 0.7
        score = min(1.0, score)
        best = max(faults.items(), key=lambda kv: kv[1])[0] if faults else "HEALTHY"
        return {
            "device_id": DEVICE_ID, "motor_id": motor_id, "ts": int(time.time() * 1000),
            "source": "edge",
            "health_index": round(max(5.0, 100.0 - 95.0 * score), 1),
            "anomaly_score": round(score, 3),
            "is_anomaly": bool(score > 0.6),
            "fault_class": best,
            "model": f"tinyml-int8-{FIRMWARE_VERSION}",
            "inference_ms": round(1.2 + random.random() * 1.4, 2),
            "memory_kb": 42,
        }

    def run(self) -> None:
        self.client.connect(self.args.host, self.args.port, keepalive=45)
        self.client.loop_start()
        self._log(f"publication sur '{TOPIC_PREFIX}/…' — {self.hz:g} Hz, moteurs {list(self.motors)} "
                  f"({'physique du projet' if PHYSICS_AVAILABLE else 'physique simplifiée'})")
        next_tick = time.time()
        last_feature = 0.0
        last_status = 0.0
        last_heartbeat = 0.0
        try:
            while not self.stop_requested:
                now = time.time()
                for motor_id, motor in self.motors.items():
                    if PHYSICS_AVAILABLE:
                        # La consigne est celle du moteur : publier une consigne unique pour M1 et M2
                        # créerait un « speed_error » fantôme côté backend.
                        telemetry = telemetry_from_physics(
                            motor, now - self.started_at, self.dt, motor.target_rpm)
                    else:
                        telemetry = motor.step(self.dt)
                    telemetry.update({"device_id": DEVICE_ID, "motor_id": motor_id,
                                      "ts": int(now * 1000), "target_speed_rpm": motor.target_rpm,
                                      "_wear": round(float(getattr(motor, "wear", 0.0)), 4)})
                    self._publish(f"{TOPIC_PREFIX}/{motor_id.lower()}/telemetry", telemetry)
                    self.rpm_windows[motor_id].append(telemetry["speed"]["rpm"])
                    self.rpm_windows[motor_id] = self.rpm_windows[motor_id][-60:]

                    # Features embarquées (1 Hz) : c'est la carte qui les calcule
                    if now - last_feature >= 1.0 and PHYSICS_AVAILABLE:
                        features = extract_from_telemetry(telemetry, None, self.rpm_windows[motor_id])
                        self._publish(f"{TOPIC_PREFIX}/{motor_id.lower()}/features", {
                            "device_id": DEVICE_ID, "motor_id": motor_id, "ts": telemetry["ts"],
                            "features": features, "firmware": FIRMWARE_VERSION,
                        })
                    if now - last_feature >= 1.0:
                        edge = self._edge_inference(motor_id, telemetry)
                        self._publish(f"{TOPIC_PREFIX}/{motor_id.lower()}/edge_inference", edge)

                if now - last_feature >= 1.0:
                    last_feature = now
                if now - last_status >= 0.2:
                    last_status = now
                    self._publish(f"{TOPIC_PREFIX}/status", {
                        "device_id": DEVICE_ID, "ts": int(now * 1000), "running": self.running,
                        "target_speed_rpm": self.speed_rpm,
                        "belt_mm_s": round(self.speed_rpm / 60.0 * BELT_MM_PER_REV, 2),
                        "load_kg": self.load_kg, "target_parcels_per_min": self.parcels_per_min,
                        "mode": "HARDWARE-SIM", "firmware": FIRMWARE_VERSION,
                    })
                if now - last_heartbeat >= 1.0:
                    last_heartbeat = now
                    self._publish(f"{TOPIC_PREFIX}/{DEVICE_ID}/heartbeat", {
                        "device_id": DEVICE_ID, "ts": int(now * 1000), "uptime_s": round(now - self.started_at, 1),
                        "firmware": FIRMWARE_VERSION, "rssi_dbm": -58, "heap_kb": 178,
                        "ip": "192.168.1.42", "mqtt_qos": 1, "sampling_hz": self.hz,
                        "motors": {mid: self._fault_label(m) for mid, m in self.motors.items()},
                    })
                self.tick += 1
                if self.args.ticks and self.tick >= self.args.ticks:
                    self.stop_requested = True
                next_tick += self.dt
                sleep_for = next_tick - time.time()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    next_tick = time.time()  # rattrapage : la cadence réelle suit l'horloge
        except KeyboardInterrupt:  # pragma: no cover
            self._log("interruption clavier")
        finally:
            self.running = False
            self._publish(f"{TOPIC_PREFIX}/status", {
                "device_id": DEVICE_ID, "ts": int(time.time() * 1000), "running": False,
                "mode": "HARDWARE-SIM", "message": "arrêt de la passerelle simulée",
            })
            time.sleep(0.3)
            self.client.loop_stop()
            self.client.disconnect()
            self._log(f"arrêté ({self.tick} trames publiées)")

    # ------------------------------------------------------- scénario de démo
    def run_scenario(self, name: str) -> None:
        """Rejoue le scénario de soutenance : mêmes jalons que la console web."""
        steps = [
            (0, "reset", None), (8, "speed", 150), (22, "fault", ("M1", "UNBALANCE", 45)),
            (40, "fault", ("M1", "UNBALANCE", 85)), (60, "fault", ("M2", "BEARING_FAULT", 55)),
            (85, "load", 1.6), (105, "fault", ("M2", "OVERHEATING", 70)),
            (125, "fault", ("M1", "BELT_SLIP", 60)), (145, "fault", ("M2", "MOTOR_STALL", 100)),
            (158, "stop", None), (165, "reset", None), (172, "start", None),
        ]
        start = time.time()
        self._log(f"scénario « {name} » démarré ({len(steps)} jalons sur 174 s)")
        client_publish = self._publish

        def announce(message: str) -> None:
            client_publish(f"{TOPIC_PREFIX}/status", {
                "device_id": DEVICE_ID, "ts": int(time.time() * 1000), "scenario": name,
                "running": self.running, "step": message, "mode": "HARDWARE-SIM",
            })
            self._log(f"  → {message}")

        for index, (at, action, data) in enumerate(steps):
            delay = start + at - time.time()
            if delay > 0:
                time.sleep(delay)
            if self._scenario_stop:
                self._log("scénario interrompu par l'opérateur")
                self._scenario_stop = False
                return
            if action == "reset":
                self._apply_command("clear_fault", {}, "all")
                self._apply_command("set_controls", {"speed_rpm": 120, "load_kg": 0.6}, "all")
                announce("Remise à zéro : ligne nominale")
            elif action == "speed":
                self._apply_command("set_controls", {"speed_rpm": data}, "all")
                announce(f"Montée en cadence ({data} rpm)")
            elif action == "load":
                self._apply_command("set_controls", {"load_kg": data}, "all")
                announce(f"Charge lourde ({data} kg)")
            elif action == "fault":
                motor_id, fault, intensity = data  # type: ignore[misc]
                self._apply_command("inject_fault",
                                    {"motor_id": motor_id, "fault": fault, "intensity": intensity},
                                    motor_id)
                announce(f"{motor_id} : {fault} à {intensity} %")
            elif action == "stop":
                self._apply_command("set_controls", {"running": False}, "all")
                announce("Arrêt de ligne (sécurité)")
            elif action == "start":
                self._apply_command("set_controls", {"running": True, "speed_rpm": 120}, "all")
                announce("Redémarrage — retour à la normale")
        self._publish(f"{TOPIC_PREFIX}/status", {
            "device_id": DEVICE_ID, "ts": int(time.time() * 1000), "scenario": name,
            "running": True, "done": True, "mode": "HARDWARE-SIM",
        })
        self._log("scénario terminé")


# =============================================================================
#  Commandes ponctuelles (publication d'un défaut sans lancer le flux)
# =============================================================================
def publish_command(args: argparse.Namespace, payload: Dict[str, Any], topic: str) -> int:
    client = make_client(f"{DEVICE_ID}-cli")
    if args.username:
        client.username_pw_set(args.username, args.password or None)
    if args.tls:
        client.tls_set(ca_certs=args.ca_cert or None)
    client.connect(args.host, args.port, keepalive=20)
    client.loop_start()
    time.sleep(0.4)
    client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1)
    time.sleep(0.5)
    client.loop_stop()
    client.disconnect()
    return 0


def send_fault(args: argparse.Namespace) -> int:
    payload = {"command": "inject_fault", "params": {
        "motor_id": args.motor.upper(), "fault": args.fault.upper(),
        "intensity": args.intensity, "degradation_rate": args.degradation,
        "auto_clear_after_s": args.auto_clear if args.auto_clear > 0 else None,
    }}
    topic = f"{TOPIC_PREFIX}/cmd/{args.motor.upper()}"
    print(f"[mqtt] injection {args.fault} ({args.intensity} %) sur {args.motor} → {topic}")
    return publish_command(args, payload, topic)


def send_control(args: argparse.Namespace) -> int:
    params: Dict[str, Any] = {}
    if args.speed is not None:
        params["speed_rpm"] = args.speed
    if args.load is not None:
        params["load_kg"] = args.load
    if args.parcels is not None:
        params["parcels_per_min"] = args.parcels
    if args.stop:
        params["running"] = False
    if args.start:
        params["running"] = True
    if not params:
        print("[mqtt] aucune consigne fournie (--speed, --load, --parcels, --start, --stop)")
        return 2
    topic = f"{TOPIC_PREFIX}/cmd/all"
    print(f"[mqtt] contrôle {params} → {topic}")
    return publish_command(args, {"command": "set_controls", "params": params}, topic)


def send_clear(args: argparse.Namespace) -> int:
    topic = f"{TOPIC_PREFIX}/cmd/all"
    print(f"[mqtt] remise à zéro des défauts → {topic}")
    return publish_command(args, {"command": "clear_fault", "params": {}}, topic)


def send_calibrate(args: argparse.Namespace) -> int:
    topic = f"{TOPIC_PREFIX}/cmd/{args.motor.upper()}"
    print(f"[mqtt] calibration de {args.motor} → {topic}")
    return publish_command(args, {"command": "calibrate", "params": {}}, topic)


def monitor(args: argparse.Namespace) -> int:
    """Écoute le flux MQTT et affiche un résumé périodique (outil de diagnostic)."""
    stats: Dict[str, Any] = {"messages": 0, "topics": {}, "last": {}, "started": time.time()}

    def on_connect(client: "mqtt.Client", _u: Any, _f: Any, reason_code: Any, *_rest: Any) -> None:
        client.subscribe(f"{TOPIC_PREFIX}/#", qos=1)
        code = int(getattr(reason_code, "value", reason_code) or 0)
        print(f"[monitor] abonné à {TOPIC_PREFIX}/# (rc={code}) — Ctrl+C pour quitter")

    def on_message(_c: "mqtt.Client", _u: Any, message: "mqtt.MQTTMessage") -> None:
        stats["messages"] += 1
        stats["topics"][message.topic] = stats["topics"].get(message.topic, 0) + 1
        try:
            stats["last"][message.topic] = json.loads(message.payload.decode("utf-8"))
        except Exception:
            stats["last"][message.topic] = {}

    client = make_client(f"{DEVICE_ID}-monitor")
    if args.username:
        client.username_pw_set(args.username, args.password or None)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()
    try:
        while True:
            time.sleep(args.interval)
            elapsed = time.time() - stats["started"]
            print(f"\n=== flux MQTT (depuis {elapsed:.0f} s) : {stats['messages']} messages, "
                  f"{stats['messages'] / max(elapsed, 1e-6):.1f} msg/s ===")
            for topic, count in sorted(stats["topics"].items()):
                last = stats["last"].get(topic) or {}
                summary = ""
                if "vibration" in last:
                    summary = (f"rms={last['vibration'].get('rms'):.4f} g  "
                               f"I={last['current'].get('rms_a'):.3f} A  "
                               f"T={last['temperature'].get('bearing_c'):.1f} °C  "
                               f"rpm={last['speed'].get('rpm'):.1f}")
                elif "health_index" in last:
                    summary = (f"santé={last['health_index']}  anomalie={last.get('anomaly_score')}  "
                               f"classe={last.get('fault_class')}  {last.get('model', last.get('model_version', ''))}")
                elif "features" in last:
                    summary = f"{len(last['features'])} caractéristiques"
                elif "running" in last:
                    summary = f"marche={last['running']} consigne={last.get('target_speed_rpm')} rpm"
                print(f"  {topic:44s} {count:6d} msg  {summary}")
    except KeyboardInterrupt:
        print("\n[monitor] arrêt")
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


def reset_demo(args: argparse.Namespace) -> int:
    """Réinitialise le banc : défauts effacés + cadence nominale."""
    send_clear(args)
    args.speed, args.load, args.parcels, args.stop, args.start = 120.0, 0.6, 12.0, False, True
    return send_control(args)


# =============================================================================
#  Interface en ligne de commande
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Simulateur de passerelle ESP32 (publication MQTT) — Conveyor AI Ops",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exemples :\n"
               "  python scripts/esp32_simulator.py --host localhost\n"
               "  python scripts/esp32_simulator.py --scenario demo-3min\n"
               "  python scripts/esp32_simulator.py fault --motor M2 --fault BEARING_FAULT --intensity 70\n"
               "  python scripts/esp32_simulator.py control --speed 150 --load 1.2\n"
               "  python scripts/esp32_simulator.py monitor\n",
    )
    # Options communes à toutes les sous-commandes
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"),
                        help="adresse du broker MQTT (défaut : localhost)")
    common.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", 1883)))
    common.add_argument("--username", default=os.environ.get("MQTT_USERNAME", "conveyor"))
    common.add_argument("--password", default=os.environ.get("MQTT_PASSWORD", "conveyor"))
    common.add_argument("--tls", action="store_true", help="activer MQTT over TLS (port 8883)")
    common.add_argument("--ca-cert", default=os.environ.get("MQTT_CA_CERT", ""), help="CA du broker")
    common.add_argument("--cert", default=os.environ.get("MQTT_CLIENT_CERT", ""))
    common.add_argument("--key", default=os.environ.get("MQTT_CLIENT_KEY", ""))
    common.add_argument("--quiet", action="store_true", help="journal minimal")

    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", parents=[common], help="publier le flux de télémétrie (défaut)")
    run.add_argument("--hz", type=float, default=float(os.environ.get("SIM_HZ", 8)))
    run.add_argument("--motors", default="M1,M2")
    run.add_argument("--speed", type=float, default=120.0, help="vitesse de consigne (rpm)")
    run.add_argument("--load", type=float, default=0.6, help="charge transportée (kg)")
    run.add_argument("--parcels", type=float, default=12.0, help="débit de colis (colis/min)")
    run.add_argument("--ticks", type=int, default=0, help="nombre de trames puis arrêt (0 = infini)")
    run.add_argument("--scenario", default="", help="rejouer un scénario (demo-3min)")

    fault = sub.add_parser("fault", parents=[common], help="injecter un défaut à distance")
    fault.add_argument("--motor", default="M1", choices=["M1", "M2", "m1", "m2"])
    fault.add_argument("--fault", default="UNBALANCE",
                       choices=["BEARING_FAULT", "UNBALANCE", "MISALIGNMENT", "BELT_SLIP",
                                "OVERLOAD", "OVERHEATING", "SENSOR_FAULT", "MOTOR_STALL",
                                "bearing_fault", "unbalance", "misalignment", "belt_slip",
                                "overload", "overheating", "sensor_fault", "motor_stall"])
    fault.add_argument("--intensity", type=float, default=70.0)
    fault.add_argument("--degradation", type=float, default=1.0, help="vitesse de dégradation (×)")
    fault.add_argument("--auto-clear", type=float, default=0.0, help="effacement auto (s), 0 = jamais")

    control = sub.add_parser("control", parents=[common], help="piloter la ligne")
    control.add_argument("--speed", type=float, default=None, help="consigne de vitesse (rpm)")
    control.add_argument("--load", type=float, default=None, help="charge (kg)")
    control.add_argument("--parcels", type=float, default=None, help="débit de colis")
    control.add_argument("--start", action="store_true")
    control.add_argument("--stop", action="store_true")

    clear = sub.add_parser("clear", parents=[common], help="effacer tous les défauts")
    clear.set_defaults()
    calib = sub.add_parser("calibrate", parents=[common], help="demander une calibration capteurs")
    calib.add_argument("--motor", default="M1", choices=["M1", "M2", "m1", "m2"])
    reset = sub.add_parser("reset", parents=[common], help="remise à zéro complète du banc")

    mon = sub.add_parser("monitor", parents=[common], help="afficher le flux MQTT (diagnostic)")
    mon.add_argument("--interval", type=float, default=5.0)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # Sous docker/journald, la sortie standard est un tube : on force le vidage ligne
    # à ligne pour que la progression du simulateur s'affiche en temps réel.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # pragma: no cover - très vieux interpréteurs / flux redirigés
        pass

    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"

    def _stop(_signum: int, _frame: Any) -> None:  # arrêt propre sur SIGTERM (docker stop)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)

    if command == "run":
        simulator = GatewaySimulator(args)
        try:
            if args.scenario:
                # Un fil de flux + le scénario joué en parallèle de la publication
                import threading

                thread = threading.Thread(target=simulator.run_scenario, args=(args.scenario,), daemon=True)
                thread.start()
            simulator.run()
        except KeyboardInterrupt:
            print("\n[mqtt] arrêt demandé")
        return 0
    if command == "fault":
        return send_fault(args)
    if command == "control":
        return send_control(args)
    if command == "clear":
        return send_clear(args)
    if command == "calibrate":
        return send_calibrate(args)
    if command == "reset":
        return reset_demo(args)
    if command == "monitor":
        return monitor(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
