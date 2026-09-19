#!/usr/bin/env bash
# =============================================================================
#  Génération d'une PKI locale pour MQTT over TLS (test et démonstration).
#
#  Usage :  cd mosquitto/certs && ./generate-certs.sh mqtt.conveyor.local
#
#  Produit : ca.crt / ca.key (autorité), server.crt / server.key (broker),
#            device.crt / device.key (client ESP32), puis affiche les empreintes.
#  En production : utilisez votre PKI d'entreprise (cf. docs/SECURITE.md).
# =============================================================================
set -euo pipefail

HOSTNAME="${1:-mqtt.conveyor.local}"
DAYS=3650

echo "[certs] génération de l'autorité de certification (CA)…"
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days "${DAYS}" -out ca.crt \
  -subj "/C=TN/ST=Tunis/L=Tunis/O=Conveyor AI Ops/CN=Conveyor-AI-Ops-CA"

echo "[certs] génération du certificat serveur (broker)…"
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr \
  -subj "/C=TN/ST=Tunis/L=Tunis/O=Conveyor AI Ops/CN=${HOSTNAME}"
cat > server.ext <<EXT
subjectAltName=DNS:${HOSTNAME},DNS:localhost,IP:127.0.0.1
extendedKeyUsage=serverAuth
EXT
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days "${DAYS}" -sha256 -extfile server.ext

echo "[certs] génération du certificat client (ESP32)…"
openssl genrsa -out device.key 2048
openssl req -new -key device.key -out device.csr \
  -subj "/C=TN/ST=Tunis/L=Tunis/O=Conveyor AI Ops/CN=esp32-conveyor-01"
cat > device.ext <<EXT
extendedKeyUsage=clientAuth
EXT
openssl x509 -req -in device.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out device.crt -days "${DAYS}" -sha256 -extfile device.ext

rm -f server.csr device.csr server.ext device.ext
chmod 600 ca.key server.key device.key

echo
echo "[certs] terminé :"
ls -1 ca.crt server.crt device.crt
echo
echo "Broker  : décommenter le bloc TLS dans mosquitto/mosquitto.conf puis relancer compose."
echo "Backend : MQTT_TLS=true, MQTT_CA_CERT=/mosquitto/certs/ca.crt"
echo "ESP32   : embarquer ca.crt (voir firmware/esp32_conveyor.ino, section TLS)."
