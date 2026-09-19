"""
Point d'entrée FastAPI — « Détection d'anomalies et maintenance prédictive d'un tapis roulant industriel ».

Démarrage (lifespan) :
  1. création des tables + amorçage de la base (moteurs, utilisateurs, règles, historique 24 h),
  2. chargement du moteur ML (artefact scikit-learn si présent, sinon modèle physique informé),
  3. chargement des règles d'alerte,
  4. connexion Redis (optionnelle) et du pont MQTT (si MQTT_ENABLED=true),
  5. démarrage du simulateur physique (MODE=SIMULATION) et du planificateur APScheduler.

Tout est conçu pour être immédiatement démontrable via `docker compose up`, sans matériel.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .db import init_db
from .routers import alerts, analytics, auth, devices, ml_ops, motors, simulator as simulator_router, system
from .services.alerting import alert_engine
from .services.common import logger, uptime_human
from .services.ml_service import ml_service
from .services.mqtt_client import mqtt_bridge
from .services.scheduler import scheduler_service
from .services.seed import seed_all
from .services.simulator import simulator

APP_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Démarrage/arrêt propres de toute la chaîne."""
    logger.info("=" * 78)
    logger.info("  %s v%s — démarrage", settings.app_name, APP_VERSION)
    logger.info("  Mode : %s | MQTT : %s | Redis : %s", settings.mode,
                "activé" if settings.mqtt_enabled else "désactivé",
                "activé" if settings.redis_enabled else "désactivé")
    logger.info("=" * 78)

    # 1) Base de données + amorçage démonstratif
    init_db()
    try:
        seed_all()
    except Exception as exc:  # pragma: no cover - le service doit démarrer malgré tout
        logger.exception("Amorçage de la base en échec : %s", exc)

    # 2) Moteur d'inférence (artefact ML ou modèle physique informé)
    ml_service.init_engine()

    # 3) Règles d'alerte
    alert_engine.load_rules()

    # 4) Infrastructures optionnelles
    await system.redis_client.connect()
    mqtt_bridge.start(asyncio.get_running_loop())

    # 5) Simulateur + planificateur
    if settings.is_simulation:
        await simulator.start()
    else:
        logger.info("MODE=HARDWARE : simulateur désactivé, attente des trames MQTT de l'ESP32")
    await scheduler_service.start()

    logger.info("Backend prêt — documentation interactive sur /docs")
    try:
        yield
    finally:
        logger.info("Arrêt du backend…")
        await scheduler_service.shutdown()
        await simulator.stop()
        mqtt_bridge.stop()
        await system.redis_client.close()
        logger.info("Arrêt terminé (uptime %s)", uptime_human(time.time() - system.STARTED_AT))


app = FastAPI(
    title=settings.app_name,
    version=APP_VERSION,
    description=(
        "API temps réel de détection d'anomalies et maintenance prédictive d'un tapis roulant "
        "industriel à deux moteurs pas à pas (M1 = gauche, M2 = droite).\n\n"
        "* Ingestion MQTT / simulateur → extraction de 21 caractéristiques → Isolation Forest + "
        "RandomForest (7 classes) → régression RUL → fusion pondérée de l'indice de santé.\n"
        "* WebSocket `/ws/telemetry` : télémétrie, inférences, alertes, scénario de démonstration.\n"
        "* REST : moteurs, analytique, alertes/règles, maintenance, devices, MLOps, simulateur, rapports PDF."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# --- Middlewares -----------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    """Ajoute le temps de traitement (ms) et journalise les requêtes lentes."""
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
    if elapsed_ms > 400:
        logger.warning("Requête lente %.1f ms : %s %s", elapsed_ms, request.method, request.url.path)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
    """Réponse JSON homogène en cas d'erreur inattendue (le front affiche un message lisible)."""
    logger.exception("Erreur non gérée sur %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Erreur interne : {exc}", "path": request.url.path})


# --- Routers ---------------------------------------------------------------
app.include_router(system.router)
app.include_router(system.ws_router)
app.include_router(auth.router)
app.include_router(motors.router)
app.include_router(analytics.router)
app.include_router(alerts.router)
app.include_router(devices.router)
app.include_router(ml_ops.router)
app.include_router(simulator_router.router)


@app.get("/", tags=["system"], summary="Racine : identité du service et liens utiles")
def root() -> Dict[str, Any]:
    return {
        "app": settings.app_name,
        "version": APP_VERSION,
        "mode": settings.mode,
        "docs": "/docs",
        "health": "/api/health",
        "overview": "/api/overview",
        "websocket": "/ws/telemetry",
        "pages": ["/", "/motors/M1", "/analytics", "/alerts", "/ml", "/devices", "/simulator", "/settings"],
        "motors": ["M1 — gauche", "M2 — droite"],
    }
