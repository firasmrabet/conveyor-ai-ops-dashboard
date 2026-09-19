#!/bin/sh
# =============================================================================
#  Point d'entrée du broker : génère le fichier de mots de passe au premier
#  démarrage (aucun secret n'est versionné) puis lance mosquitto.
# =============================================================================
set -e

PASSWD_FILE="/mosquitto/data/passwd"
GATEWAY_USER="${MQTT_USERNAME:-conveyor}"
GATEWAY_PASS="${MQTT_PASSWORD:-conveyor}"
BACKEND_USER="${MQTT_BACKEND_USERNAME:-conveyor-backend}"
BACKEND_PASS="${MQTT_BACKEND_PASSWORD:-$GATEWAY_PASS}"
MONITOR_USER="${MQTT_MONITOR_USERNAME:-conveyor-monitor}"
MONITOR_PASS="${MQTT_MONITOR_PASSWORD:-monitor}"

if [ ! -s "${PASSWD_FILE}" ]; then
  echo "[mosquitto] création du fichier de mots de passe (${PASSWD_FILE})"
  touch "${PASSWD_FILE}"
  mosquitto_passwd -b -c "${PASSWD_FILE}" "${GATEWAY_USER}" "${GATEWAY_PASS}"
  mosquitto_passwd -b "${PASSWD_FILE}" "${BACKEND_USER}" "${BACKEND_PASS}"
  mosquitto_passwd -b "${PASSWD_FILE}" "${MONITOR_USER}" "${MONITOR_PASS}"
  chown mosquitto:mosquitto "${PASSWD_FILE}" 2>/dev/null || true
  chmod 600 "${PASSWD_FILE}"
else
  echo "[mosquitto] fichier de mots de passe existant conservé"
fi

echo "[mosquitto] comptes : ${GATEWAY_USER} (passerelle), ${BACKEND_USER} (backend), ${MONITOR_USER} (lecture)"
exec mosquitto -c /mosquitto/config/mosquitto.conf
