#!/usr/bin/env bash
# =============================================================================
#  Point d'entrée backend : garantit qu'un modèle d'IA exploitable est présent,
#  puis démarre l'API (ou la commande transmise par docker-compose).
#
#  Le modèle est chargé depuis ./artifacts. S'il est absent ou illisible avec la
#  version de scikit-learn de l'image, il est réentraîné à la volée sur les
#  données synthétiques du modèle physique du convoyeur.
# =============================================================================
set -euo pipefail

BUNDLE="${ML_ACTIVE_BUNDLE:-conveyor-synth}"
ARTIFACT="./artifacts/${BUNDLE}.joblib"

if [ -f "${ARTIFACT}" ]; then
  if python - "${ARTIFACT}" <<'PY'
import sys
import joblib
path = sys.argv[1]
bundle = joblib.load(path)
assert "scaler" in bundle and "classifier" in bundle, "artefact incomplet"
print(f"[entrypoint] modèle chargé : {bundle.get('version', '?')} ({path})")
PY
  then
    echo "[entrypoint] artefact ML validé."
  else
    echo "[entrypoint] artefact illisible : réentraînement des modèles…"
    python -m ml.train --samples 120 --out ./artifacts \
      --version "${ML_MODEL_VERSION:-v1.4.0}" --seed 7 || \
      echo "[entrypoint] réentraînement impossible : le modèle heuristique (physique) prendra le relais."
  fi
else
  echo "[entrypoint] aucun artefact trouvé : entraînement initial…"
  python -m ml.train --samples 120 --out ./artifacts \
    --version "${ML_MODEL_VERSION:-v1.4.0}" --seed 7 || \
    echo "[entrypoint] entraînement impossible : le modèle heuristique (physique) prendra le relais."
fi

# Attente du broker MQTT (facultatif : l'API démarre même sans broker)
if [ "${MQTT_ENABLED:-false}" = "true" ] && [ -n "${MQTT_HOST:-}" ]; then
  echo "[entrypoint] attente du broker MQTT ${MQTT_HOST}:${MQTT_PORT:-1883}…"
  for _ in $(seq 1 20); do
    if python - "${MQTT_HOST}" "${MQTT_PORT:-1883}" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
with socket.create_connection((host, port), timeout=1):
    pass
PY
    then
      echo "[entrypoint] broker MQTT joignable."
      break
    fi
    sleep 1
  done
fi

exec "$@"
