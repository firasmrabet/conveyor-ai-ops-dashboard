# =============================================================================
#  Backend FastAPI — ingestion MQTT, IA, alertes, maintenance, rapports PDF
#  Image : python:3.11-slim (compatible avec les exigences du cahier des charges)
# =============================================================================
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Africa/Tunis

WORKDIR /app

# --- Dépendances système -----------------------------------------------------
# build-essential : roues numpy/scikit-learn si pas de wheel précompilée
# curl            : sonde de santé Docker
# tzdata          : horodatage local (Tunis) dans les rapports
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# --- Dépendances Python (couche mise en cache par Docker) --------------------
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# --- Code applicatif ---------------------------------------------------------
COPY . .

# --- Modèles d'IA ------------------------------------------------------------
# L'artefact entraîné est livré avec le projet. On vérifie qu'il est CHARGEABLE
# avec la version de scikit-learn de l'image (garantie de reproductibilité) ;
# s'il ne l'est pas (ou s'il est absent), on réentraîne immédiatement sur les
# données synthétiques physiques — l'image reste ainsi toujours opérationnelle.
RUN python -m ml.validate --artifacts ./artifacts \
    || python -m ml.train --samples 120 --out ./artifacts --version v1.4.0 --seed 7

# --- Point d'entrée ----------------------------------------------------------
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=6s --start-period=30s --retries=4 \
  CMD curl -fsS http://localhost:8000/api/health || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
