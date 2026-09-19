"""Client MQTT (paho-mqtt) : abonnement aux topics ESP32 et publication des commandes.

Fonctionne dans un thread dédié et repasse les messages dans la boucle asyncio
principale (`asyncio.run_coroutine_threadsafe`) pour alimenter le pipeline
d'ingestion sans bloquer le serveur.

Topics :
    conveyor/m1/telemetry        conveyor/m1/features        conveyor/m1/edge_inference
    conveyor/m2/telemetry        conveyor/m2/features        conveyor/m2/edge_inference
    conveyor/status              conveyor/+/heartbeat        conveyor/cmd/# (descendant)
"""
from __future__ import annotations

import asyncio
import json
import ssl
import threading
import time
from typing import Any, Dict, List, Optional

import paho.mqtt.client as mqtt

from ..config import settings
from ..realtime import hub
from .common import logger
from .ingestion import handle_message


#: Délai de reconnexion (s) utilisé par le repli manuel quand la librairie ne
#: réessaie pas automatiquement (le client paho possède aussi son propre backoff).
RECONNECT_DELAY_S = 10.0


class MQTTBridge:
    """Pont MQTT ↔ API. Désactivable : l'app reste pleinement fonctionnelle sans broker."""

    def __init__(self) -> None:
        self.client: Optional[mqtt.Client] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self.connected = False
        self.status = "DISABLED"
        self.messages = 0
        self.bytes_received = 0
        self.last_message_ts: float = 0.0
        self.errors = 0
        self.subscriptions: List[str] = []
        self._pending: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Démarre le pont si MQTT est activé dans la configuration."""
        self.loop = loop
        if not settings.mqtt_enabled:
            self.status = "DISABLED"
            logger.info("MQTT désactivé (MQTT_ENABLED=false) — l'application continue sans broker")
            return
        try:
            self.client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=settings.mqtt_client_id,
                protocol=mqtt.MQTTv311,
                clean_session=True,
            )
            if settings.mqtt_username:
                self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
            if settings.mqtt_tls:
                self.client.tls_set(ca_certs=settings.mqtt_ca_cert or None, tls_version=ssl.PROTOCOL_TLS_CLIENT)
                self.client.tls_insecure_set(False)

            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

            self.thread = threading.Thread(target=self._run, name="mqtt-bridge", daemon=True)
            self.thread.start()
            self.status = "CONNECTING"
            logger.info("Pont MQTT démarré (%s:%s, TLS=%s)", settings.mqtt_host, settings.mqtt_port,
                        settings.mqtt_tls)
        except Exception as exc:  # pragma: no cover
            self.status = "ERROR"
            self.errors += 1
            logger.error("Initialisation MQTT échouée : %s", exc)

    def stop(self) -> None:
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        self.connected = False
        self.status = "STOPPED"

    # ------------------------------------------------------------------
    def _run(self) -> None:
        while True:
            try:
                assert self.client is not None
                self.client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=30)
                self.client.loop_forever(retry_first_connection=True)
            except Exception as exc:  # pragma: no cover
                self.errors += 1
                logger.warning("MQTT déconnecté (%s) — nouvelle tentative dans 10 s", exc)
                time.sleep(10)

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, rc: Any,
                    properties: Any = None) -> None:
        """Callback paho-python.

        Signature paho-mqtt 2.x : `(client, userdata, flags, reason_code, properties)`.
        Le code de retour est soit un entier (paho 1.x), soit un objet `ReasonCode`
        (paho 2.x) dont la valeur 0 signifie « connexion acceptée ».
        """
        ok = (getattr(rc, "value", rc) == 0) or (rc == 0)
        self.connected = bool(ok)
        if not ok:
            self.status = "ERROR"
            self.errors += 1
            logger.error("Connexion MQTT refusée : rc=%s", rc)
            return
        self.status = "ONLINE"
        prefix = settings.mqtt_topic_prefix
        topics = [(f"{prefix}/+/telemetry", settings.mqtt_qos),
                  (f"{prefix}/+/features", settings.mqtt_qos),
                  (f"{prefix}/+/edge_inference", settings.mqtt_qos),
                  (f"{prefix}/+/heartbeat", settings.mqtt_qos),
                  (f"{prefix}/status", settings.mqtt_qos)]
        for topic, qos in topics:
            client.subscribe(topic, qos=qos)
            self.subscriptions.append(topic)
        logger.info("MQTT connecté — abonné à %s", ", ".join(t for t, _ in topics))

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, disconnect_flags: Any = None,
                       rc: Any = None, properties: Any = None) -> None:
        """Callback paho-python.

        Signature paho-mqtt 2.x (5 arguments) : `(client, userdata, disconnect_flags,
        reason_code, properties)`. En paho 1.x le troisième argument était le code de
        retour : la méthode tolère donc les deux formes (`*args`) — sans cela, la
        déconnexion levait `TypeError` et le pont MQTT ne repartait jamais, ce qui
        rendait le mode HARDWARE inutilisable.
        """
        self.connected = False
        self.status = "RECONNECTING"
        code = getattr(rc, "value", rc)
        if code is None:                       # paho 1.x : code transmis en 3e position
            code = getattr(disconnect_flags, "value", disconnect_flags)
        logger.warning("MQTT déconnecté (code %s) — reconnexion automatique dans %s s",
                       code, RECONNECT_DELAY_S)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        self.messages += 1
        self.bytes_received += len(msg.payload or b"")
        self.last_message_ts = time.time()
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            logger.warning("Payload MQTT non-JSON ignoré (%s)", msg.topic)
            return
        hub.tick_counters(n_bytes=len(msg.payload or b""))
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(handle_message(msg.topic, payload), self.loop)

    # ------------------------------------------------------------------
    def publish_json(self, topic: str, payload: Dict[str, Any], qos: Optional[int] = None) -> bool:
        """Publication (simulateur, commandes descendantes)."""
        if not self.client or not self.connected:
            return False
        try:
            info = self.client.publish(topic, json.dumps(payload, default=str),
                                       qos=qos if qos is not None else settings.mqtt_qos)
            return info.rc == 0
        except Exception as exc:  # pragma: no cover
            logger.debug("Publication MQTT échouée : %s", exc)
            return False

    def send_command(self, target: str, command: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Commande descendante : conveyor/cmd/{target}/{command}."""
        topic = f"{settings.mqtt_topic_prefix}/cmd/{target}/{command}"
        payload = {"command": command, "target": target, "ts": int(time.time() * 1000), **(params or {})}
        published = self.publish_json(topic, payload)
        logger.info("Commande %s → %s (mqtt=%s)", command, target, published)
        return {"topic": topic, "payload": payload, "published": published}

    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": settings.mqtt_enabled,
            "status": self.status,
            "connected": self.connected,
            "host": settings.mqtt_host,
            "port": settings.mqtt_port,
            "tls": settings.mqtt_tls,
            "client_id": settings.mqtt_client_id,
            "messages": self.messages,
            "bytes_received": self.bytes_received,
            "last_message_s": round(time.time() - self.last_message_ts, 1) if self.last_message_ts else None,
            "errors": self.errors,
            "subscriptions": self.subscriptions,
        }


mqtt_bridge = MQTTBridge()
