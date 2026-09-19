#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Broker MQTT de test (sans Docker)
=================================

Lance un broker MQTT local compatible avec le banc Conveyor AI Ops, sans installer
Mosquitto : pratique pour valider toute la chaîne matérielle là où Docker n'est pas
disponible (poste de développement, conteneur d'essai, CI).

Ce que ce broker permet de vérifier, **avec exactement les mêmes topics et payloads
que l'ESP32 réel** :

    ESP32 (ou `scripts/esp32_simulator.py`) → broker → backend FastAPI → WebSocket → interface

Différences assumées avec le broker de production (`mosquitto/mosquitto.conf`) :

| Point | Ici (test) | Production (Mosquitto) |
|---|---|---|
| Authentification | anonyme acceptée (option `--require-auth` pour l'exiger) | comptes obligatoires, un par device |
| ACL par topic | désactivée | `mosquitto/acl` (moindre privilège) |
| TLS | non | port 8883 + certificats (`mosquitto/certs`) |

Utilisation :
    python tools/mqtt_broker_test.py                       # 0.0.0.0:1883, anonyme
    python tools/mqtt_broker_test.py --require-auth        # exige un utilisateur/mot de passe
    python tools/mqtt_broker_test.py --port 1884 --quiet

Équivalent en production : `docker compose up mosquitto`.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Any, Dict

try:
    from amqtt.broker import Broker
except ImportError:  # pragma: no cover - dépendance de test facultative
    print("amqtt est requis : pip install amqtt==0.11.3", file=sys.stderr)
    raise SystemExit(2)


def build_config(host: str, port: int, require_auth: bool) -> Dict[str, Any]:
    """Configuration du broker au format amqtt ≥ 0.11 (section `plugins`).

    La section `plugins` remplace les anciennes clés `auth` / `topic-check`
    dépréciées : c'est le seul format qui déclenche réellement le chargement des
    greffons d'authentification (sans lui, le broker refuse toute connexion avec
    le code 128 « unspecified error »).
    """
    plugins: Dict[str, Any] = {
        # Le plugin sys est désactivé : inutile pour un broker de test, et bruyant.
        "amqtt.plugins.sys.broker.BrokerSysPlugin": {"sys_interval": 0},
        # Contrôle de topics volontairement désactivé : le banc de test doit accepter
        # les publications `conveyor/#` sans configuration d'ACL.
        "amqtt.plugins.topic_checking.TopicTabooPlugin": {},
    }
    if require_auth:
        # Refuse les clients anonymes (le device doit présenter un couple utilisateur/mot de passe).
        plugins["amqtt.plugins.authentication.AnonymousAuthPlugin"] = {"allow_anonymous": False}
    else:
        # Accepte les clients anonymes ET authentifiés : le backend peut donc tourner
        # avec ou sans MQTT_USERNAME/MQTT_PASSWORD pendant la mise au point.
        plugins["amqtt.plugins.authentication.AnonymousAuthPlugin"] = {"allow_anonymous": True}

    return {
        "listeners": {
            "default": {
                "type": "tcp",
                "bind": f"{host}:{port}",
                "max_connections": 200,
            },
        },
        "plugins": plugins,
    }


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Broker MQTT de test (sans Docker)")
    parser.add_argument("--host", default="0.0.0.0", help="interface d'écoute (0.0.0.0 = accessible depuis le réseau local)")
    parser.add_argument("--port", type=int, default=1883, help="port TCP (1883 par défaut)")
    parser.add_argument("--require-auth", action="store_true",
                        help="refuse les clients anonymes (le device doit s'authentifier)")
    parser.add_argument("--quiet", action="store_true", help="journal minimal")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    if args.quiet:
        logging.getLogger("amqtt").setLevel(logging.WARNING)

    broker = Broker(build_config(args.host, args.port, args.require_auth))
    await broker.start()
    print(f"[broker] MQTT de test à l'écoute sur mqtt://{args.host}:{args.port} "
          f"(authentification {'exigée' if args.require_auth else 'anonyme acceptée'})", flush=True)
    print("[broker] production : docker compose up mosquitto → mosquitto/mosquitto.conf", flush=True)

    stop = asyncio.Event()

    def request_stop(*_: Any) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:  # pragma: no cover - Windows
            signal.signal(sig, request_stop)

    await stop.wait()
    await broker.shutdown()
    print("[broker] arrêté proprement", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
