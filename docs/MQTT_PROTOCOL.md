# Protocole MQTT — Conveyor AI Ops

Contrat d'interface entre la passerelle ESP32 (ou `scripts/esp32_simulator.py`) et le backend.
Il est **identique dans les trois modes** (`SIMULATION`, `HARDWARE`, `HYBRID`) : c'est ce qui
permet de valider toute la chaîne sans matériel puis de brancher la carte sans changer de code.

---

## 1. Broker et sécurité

| Élément | Valeur |
|---|---|
| Broker | Eclipse Mosquitto (service `mosquitto` de `docker-compose.yml`) |
| Port TCP | `1883` |
| Port WebSocket | `9001` (utile pour un client navigateur) |
| Port TLS | `8883` (bloc commenté à activer : `mosquitto/certs/generate-certs.sh`) |
| Authentification | Nom d'utilisateur / mot de passe **obligatoire** (`allow_anonymous false`) |
| Comptes créés au premier démarrage | `conveyor` (devices), `conveyor-backend` (service), `conveyor-monitor` (supervision) |
| Droits (ACL) | Moindre privilège : un device ne publie que sur ses topics et ne lit que `conveyor/cmd/<son id>`, `conveyor/config/#`, `conveyor/status` |
| QoS | 1 (au moins une fois) pour la télémétrie et les commandes, 0 pour le statut |
| Last Will | `conveyor/status` avec `{"running": false, "mode": "OFFLINE"}` → le backend détecte la coupure d'une carte |
| Persistance | Activée (`/mosquitto/data`), 10 000 messages en file, file de session pour les clients persistants |

Vérifications :

```bash
# S'abonner à tout le trafic (le plus simple pour diagnostiquer)
docker compose exec mosquitto mosquitto_sub -u conveyor-monitor -P conveyor-monitor -t 'conveyor/#' -v

# Publier une commande à la main
docker compose exec mosquitto mosquitto_pub -u conveyor -P conveyor \
  -t 'conveyor/cmd/M1/inject_fault' -m '{"command":"inject_fault","target":"M1","fault":"UNBALANCE","intensity":85}'
```

---

## 2. Topics montants

### 2.1 `conveyor/m1/telemetry` · `conveyor/m2/telemetry` — 8 Hz (5-10 Hz)

Payload complet, conforme au cahier des charges :

```json
{
  "device_id": "esp32-conveyor-01",
  "motor_id": "M1",
  "ts": 1789583096069,
  "vibration": {
    "ax": 0.0412, "ay": 0.0471, "az": 1.0132,
    "rms": 0.0662, "peak": 0.2145, "kurtosis": 2.94, "crest_factor": 3.24
  },
  "fft_peaks": [
    {"freq": 2.0,  "amp": 0.0481},
    {"freq": 4.0,  "amp": 0.0152},
    {"freq": 6.0,  "amp": 0.0081},
    {"freq": 9.6,  "amp": 0.0044},
    {"freq": 14.4, "amp": 0.0026}
  ],
  "current": {"rms_a": 0.943, "peak_a": 1.338, "power_w": 11.32},
  "temperature": {"motor_c": 41.5, "bearing_c": 38.9, "ambient_c": 24.0},
  "acoustic": {"spl_db": 52.4, "band_energy": [0.021, 0.044, 0.018, 0.006]},
  "speed": {"rpm": 120.02, "steps_per_s": 640.1, "belt_mm_s": 84.01},
  "state": "RUNNING"
}
```

| Champ | Unité | Remarques de terrain |
|---|---|---|
| `vibration.ax/ay/az` | g | Valeurs dynamiques (le 1 g statique est retiré par calibration). |
| `vibration.rms` / `peak` | g | Sur la fenêtre glissante (2 048 échantillons ≈ 4 s à 500 Hz). |
| `vibration.kurtosis` | — | 3,0 = signal gaussien sain ; > 4,5 signale des chocs (roulement). |
| `fft_peaks` | Hz / g | 16 pics maximum, triés par amplitude décroissante. |
| `current.power_w` | W | Estimée côté carte (`U·I` avec prise en compte de la charge). |
| `temperature.*` | °C | Sonde moteur, sonde pallier, sonde ambiante (3 × DS18B20). |
| `acoustic.spl_db` | dB | Micro MEMS optionnel ; le backend estime la valeur si absente. |
| `speed.steps_per_s` | pas/s | Impulsions réellement générées (utile au diagnostic de pas perdus). |
| `speed.belt_mm_s` | mm/s | `rpm / 60 × 42 mm` (développement de la poulie). |
| `state` | — | `RUNNING` \| `STOPPED` \| `FAULT` \| `CALIBRATING` \| `UNKNOWN`. |

**Tolérance de validation** : tout payload non conforme est rejeté avec un avertissement
(le compteur d'erreurs est visible dans `GET /api/health` → `mqtt.errors`). Les champs manquants
ont des valeurs par défaut : une carte partielle n'interrompt donc jamais l'ingestion.

### 2.2 `conveyor/{m1|m2}/features` — 1 Hz

Les **21 caractéristiques** calculées par la carte, strictement dans l'ordre de
`backend/ml/features.py :: FEATURE_NAMES` :

```json
{
  "device_id": "esp32-conveyor-01", "motor_id": "M1", "ts": 1789583096069, "window_s": 1.0,
  "features": {
    "vibration_rms": 0.06620, "vibration_peak": 0.21450, "kurtosis": 2.9430, "crest_factor": 3.2400,
    "h1_ratio": 0.7265, "h2_ratio": 0.2296, "hf_ratio": 0.1700, "band_hf_energy": 0.0231,
    "current_rms": 0.9430, "current_peak": 1.3380, "power_w": 11.320, "current_ripple": 0.2952,
    "motor_temp": 41.50, "bearing_temp": 38.90, "temp_delta": 17.50,
    "rpm": 120.02, "speed_error": 0.020, "rpm_variance": 0.0004, "belt_speed_error": 0.0000,
    "spl_db": 52.40, "load_estimate": 0.470
  }
}
```

Le backend utilise ces valeurs **telles quelles** s'il les reçoit (mode `HARDWARE`) ; sinon il
les recalcule depuis la télémétrie. C'est ce qui rend la comparaison *edge vs serveur* de la
page `/ml` pertinente : les deux diagnostics portent sur le même vecteur.

### 2.3 `conveyor/{m1|m2}/edge_inference` — 1 Hz

Pré-diagnostic embarqué (TinyML) :

```json
{
  "device_id": "esp32-conveyor-01", "motor_id": "M1", "ts": 1789583096069,
  "health_index": 92.5, "anomaly_score": 0.31, "is_anomaly": false,
  "fault_class": "HEALTHY", "model_version": "tinyml-rules-1.0.0",
  "inference_ms": 1.83, "confidence": 0.62, "source": "edge"
}
```

### 2.4 `conveyor/status` — 5 Hz

```json
{
  "device_id": "esp32-conveyor-01", "ts": 1789583096069, "running": true,
  "target_speed_rpm": 120.0, "belt_mm_s": 84.0, "load_kg": 0.6,
  "target_parcels_per_min": 12.0, "mode": "AUTO", "firmware": "1.0.0"
}
```

### 2.5 `conveyor/{device_id}/heartbeat` — 1 Hz

```json
{
  "device_id": "esp32-conveyor-01", "ts": 1789583096069, "uptime_s": 4213.5,
  "rssi_dbm": -58, "firmware": "1.0.0", "ip": "192.168.1.42", "free_heap_kb": 178.4,
  "sampling_hz": 500, "sensors": 3, "mpu_ok": true, "emergency_stop": false, "tamper": false,
  "motors": {"M1": {"fault": "NONE", "rpm": 120.02, "steps": 1450233}}
}
```

Le backend marque une carte `OFFLINE` si aucun heartbeat n'est reçu pendant 15 s
(tâche APScheduler de supervision) et affiche l'état sur `/devices`.

### 2.6 Accusés de réception (carte → backend)

* `conveyor/{device_id}/command_ack` : `{"command": "calibrate", "status": "ACK", "message": "…"}`
* `conveyor/{device_id}/network_test` : `{"broker_reachable": true, "latency_ms": 9, "rssi_dbm": -58, "free_heap_kb": 178.4}`

---

## 3. Topics descendants

Format unique : `conveyor/cmd/{target}/{command}` où `target` ∈ `{M1, M2, all}` et où le
payload contient toujours `command`, `target`, `ts` puis les paramètres.

```json
{"command": "set_controls", "target": "all", "ts": 1789583096069,
 "running": true, "speed_rpm": 140.0, "load_kg": 1.0, "parcels_per_min": null, "ramp_s": 2.0}
```

| Commande | Paramètres | Effet sur la carte | Effet en mode SIMULATION |
|---|---|---|---|
| `set_controls` | `running`, `speed_rpm`, `load_kg`, `parcels_per_min` | Consigne appliquée aux deux moteurs (synchronisés) | Physique interne mise à jour |
| `calibrate` | — | Zéro courant + offset accéléromètre, ligne momentanément à l'arrêt | Identique (sans coupure) |
| `set_sampling` | `hz` (10-1000) | Nouvelle cadence d'acquisition (la télémétrie reste à 4-8 Hz) | Cadence du générateur |
| `reboot` | — | `ESP.restart()` après 1 s (accusé publié avant) | Réinitialisation de la physique |
| `ota_update` | `version` | Préparation de la mise à jour OTA (voir `docs/HARDWARE.md`) | Journalisation |
| `test_network` | — | Mesure latence/RSSI et publication du résultat | Identique |
| `inject_fault` | `fault`, `intensity`, `degradation_rate`, `auto_clear_after_s` | Défaut de substitution (banc, `ENABLE_BENCH_TEST`) | Défaut injecté dans la physique |
| `clear_fault` | — | Efface tous les défauts | Retour au nominal |
| `run_scenario` | `name` | Rejoue la démo de 3 minutes sur la carte | Scénario interne du backend |
| `stop_scenario` | — | Interrompt le scénario en cours | Idem |
| `fault_sequence` | `motor_id`, `step_s` | Rotation automatique des 8 défauts | Identique |

**Comportement du backend en `MODE=HARDWARE`** : les endpoints `/api/simulator/control`,
`/inject-fault`, `/clear-faults`, `/scenario`, `/stop-scenario` et `/fault-sequence` ne
pilotent plus de physique locale — ils **relaient** la commande sur MQTT et renvoient :

```json
{
  "mode": "HARDWARE", "available": false,
  "forwarded": {
    "topic": "conveyor/cmd/M1/inject_fault",
    "payload": {"command": "inject_fault", "target": "M1", "ts": 1789583196625,
                "fault": "BEARING_FAULT", "intensity": 70.0, "degradation_rate": 1.0,
                "auto_clear_after_s": null},
    "published": true
  },
  "detail": "Commande inject_fault publiée sur conveyor/cmd/M1. Le device applique le défaut puis la télémétrie revient par MQTT."
}
```

`"published": false` signifie que le broker est injoignable : l'API le signale explicitement
plutôt que de laisser croire à une commande appliquée.

---

## 4. Sécurité et exploitation

* **Isolation** : les devices n'ont pas accès aux topics des autres bancs (`acl` généré pour
  `conveyor/{device_id}`) ; le compte de supervision est en lecture seule.
* **TLS** : générer une CA locale (`mosquitto/certs/generate-certs.sh`), décommenter le bloc
  `listener 8883` et passer `MQTT_TLS=true` + `MQTT_CA_CERT` côté backend, `MQTT_USE_TLS 1`
  dans le firmware.
* **Rotation de clés** : `POST /api/devices/esp32-conveyor-01/command` avec
  `{"action":"rotate_key"}` journalise l'événement de sécurité et émet un nouveau jeton device.
* **Détection de sabotage** : le firmware lève `tamper` si la configuration NVS ou le boîtier
  est ouvert ; l'état est repris dans le heartbeat et affiché sur `/devices`.
* **Supervision du broker** : les topics `$SYS/#` sont accessibles au compte `conveyor-monitor`
  (nombre de clients, messages/s, octets) pour diagnostiquer une saturation du réseau.
* **Dimensionnement** : à 2 moteurs × 8 Hz, le flux représente ~20 messages/s et ~28 Ko/s ;
  le broker tient sans difficulté plusieurs dizaines de bancs sur le même serveur.

---

## 5. Validation rapide du contrat

```bash
# 1. Le simulateur publie exactement le protocole attendu
cd backend && python ../scripts/esp32_simulator.py run --host localhost --hz 8

# 2. Le moniteur affiche un résumé par topic (débit, dernier payload)
python ../scripts/esp32_simulator.py monitor

# 3. Le backend doit passer « MQTT : ONLINE » et recevoir les trames
curl -s localhost:8000/api/health | grep -o '"mqtt":"[A-Z]*"'

# 4. Les valeurs acceptées par la validation apparaissent dans les buffers
curl -s 'localhost:8000/api/motors/M1/telemetry?from=0' | head -c 400
```

Un test d'acceptation automatisé (mode `HARDWARE` complet : télémétrie, features, edge, statut,
heartbeat, injection de défaut relayée) est décrit dans `docs/DEMO_SOUTENANCE.md` § 4.
