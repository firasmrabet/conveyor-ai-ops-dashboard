# Intégration matérielle — Conveyor AI Ops

Guide complet pour passer du banc simulé au banc réel : câblage, calibration, mise en service,
mise à jour OTA, sécurité MQTT et adaptation des modèles aux mesures du terrain.

---

## 1. Liste du matériel

| Référence | Quantité | Rôle |
|---|---|---|
| ESP32-DevKitC v4 (ou équivalent WROOM-32) | 1 | Passerelle : acquisition, calcul embarqué, MQTT |
| MPU6050 (module GY-521) | 1 | Vibration 3 axes (ax, ay, az) |
| ACS712-20A | 2 | Courant moteur M1 et M2 |
| DS18B20 étanche (câble) | 3 | Températures corps moteur, pallier, ambiance |
| Résistance 4,7 kΩ | 1 | Tirage du bus 1-Wire |
| Driver A4988 (ou DRV8825) | 2 | Commande des moteurs pas à pas |
| Moteur NEMA17 42HD4027 | 2 | Entraînement gauche (M1) et droit (M2) |
| Alimentation 12 V / 5 A | 1 | Moteurs |
| Condensateurs 100 µF | 2 | Découplage de l'alimentation des drivers |
| INMP441 (option) | 1 | Niveau acoustique et bandes d'énergie |
| Roue codeuse 20 impulsions/tour (option) | 1 | Mesure réelle de vitesse → détection du glissement |
| Rouleaux, bande (1200 mm), châssis | — | Banc mécanique |

---

## 2. Câblage

```
                     +5 V ─────────────────────────────────────────────┐
                                                                       │
   ┌──────────────┐   SDA  GPIO21                    ┌──────────────┐  │
   │  MPU6050     ├───SCL  GPIO22────────────────────│    ESP32     │  │
   │  (GY-521)    │   VCC 3V3                       │  DevKitC v4  │  │
   └──────────────┘   GND ──────────────────────────│              │  │
                                                    │              │  │
   ┌──────────────┐   OUT ────── GPIO36 (ADC1_CH0)  │              │  │
   │ ACS712 M1    ├───VCC 5V ───────────────────────│              │  │
   └──────────────┘   GND ──────────────────────────│              │  │
   ┌──────────────┐   OUT ────── GPIO39 (ADC1_CH3)  │              │  │
   │ ACS712 M2    ├───VCC 5V ───────────────────────│              │  │
   └──────────────┘   GND ──────────────────────────│              │  │
                                                    │              │  │
   ┌──────────────┐                                 │              │  │
   │ 3 × DS18B20  ├───DQ ─────── GPIO4 (bus 1-Wire) │              │  │
   │  (parallèle) │   VCC 3V3                       │              │  │
   └──────────────┘   GND ──────────────────────────│              │  │
        │ 4,7 kΩ entre DQ et 3V3                    │              │  │
        └──────────────────────────────────────────┐│              │  │
                                                   ││              │  │
   ┌──────────────┐  STEP ── GPIO26   ┌────────────┴┴───────────┐  │  │
   │ A4988 M1     ├──DIR  ── GPIO27   │  NB : masse commune     │  │  │
   │              ├──EN   ── GPIO14   │  obligatoire entre le    │  │  │
   └──────┬───────┘                  │  12 V et l'ESP32         │  │  │
          └─ VDD 5V ─────────────────┴──────────────────────────┘  │  │
                                                                   │  │
   ┌──────────────┐  STEP ── GPIO13                                │  │
   │ A4988 M2     ├──DIR  ── GPIO12                                │  │
   │              ├──EN   ── GPIO15                                │  │
   └──────┬───────┘                                                │  │
          └─ VDD 5V                                                │  │
                                                                   │  │
   ┌──────────────┐  SCK ── GPIO32                                │  │
   │ INMP441      ├──WS  ── GPIO25                                │  │
   │ (option)     ├──SD  ── GPIO33                                │  │
   └──────────────┘                                                │  │
                                                                   │  │
   Bouton arrêt d'urgence (NF) : GPIO5 ↔ GND      LED d'état : GPIO2 ┘  │
   Encodeur (option) : A ↔ GPIO18                                        │
```

**Règles de câblage impératives**

1. **Masse commune** entre l'alimentation 12 V des moteurs et l'ESP32 : sans elle, les mesures
   de courant et de vibration sont inexploitables.
2. **Ne jamais alimenter les moteurs par le 5 V de l'ESP32** : le pic d'appel d'un pas à pas
   réinitialise la carte (les redémarrages intempestifs se voient dans l'uptime du heartbeat).
3. **Éloigner** les câbles STEP/DIR des fils d'accéléromètre ; torsader les paires et croiser
   les nappes à angle droit. Le bruit de commutation est la première cause de faux positifs.
4. **Condensateurs 100 µF** aux bornes VMOT/GND de chaque A4988.
5. **Sortie ACS712 à 5 V** : prévoir un pont diviseur 5 V → 3,3 V (rapport 0,66) avant GPIO36/39,
   sinon la tension dépasse la limite de l'ADC (et la sensibilité devient 66 mV/A, valeur déjà
   configurée par `ACS712_SENSITIVITY_V_PER_A`).
6. **MPU6050** : monter le capteur **le plus près possible** du pallier à surveiller, axe Z
   perpendiculaire au châssis, platine rigide (jamais sur une surface souple : on mesurerait la
   fixation, pas la machine).

---

## 3. Compilation et téléversement

### Arduino IDE

1. Installer le gestionnaire de cartes ESP32 (`Fichier → Préférences → URL supplémentaires` :
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`).
2. Installer les bibliothèques : `PubSubClient`, `ArduinoJson` (7.x), `OneWire`,
   `DallasTemperature`, `arduinoFFT`.
3. Ouvrir `firmware/esp32_conveyor.ino`, carte **ESP32 Dev Module**, 240 MHz, flash 4 Mo,
   partition « Minimal SPIFFS (1.9 MB APP / 190 KB SPIFFS) », port de téléversement 921600.
4. Téléverser et ouvrir le moniteur série à **115200 bauds** : le démarrage affiche la détection
   des capteurs, l'adresse IP, la connexion MQTT et la cadence d'échantillonnage.

Le sketch **compile même sans aucun capteur branché** : les composants absents sont signalés
(`MPU6050 absent sur le bus I2C`, `0 sonde(s) DS18B20 détectée(s)`) et la carte publie des trames
marquées en conséquence. C'est le mode utilisé pour valider le câblage progressivement.

### PlatformIO

```bash
cd firmware
pio run                 # compilation
pio run -t upload       # téléversement
pio device monitor -b 115200
```

---

## 4. Configuration avant mise en service

Modifier en tête de `esp32_conveyor.ino` :

| Constante | À adapter |
|---|---|
| `WIFI_SSID`, `WIFI_PASSWORD` | Réseau de l'atelier |
| `MQTT_HOST`, `MQTT_PORT` | Adresse du serveur Mosquitto |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | Compte device créé par `mosquitto/entrypoint.sh` |
| `MQTT_USE_TLS` | `1` après avoir installé les certificats (voir § 6) |
| `SAMPLING_HZ` | 500 Hz convient à un tapis 120 rpm ; 1000 Hz si bande passante et I2C le permettent |
| `FFT_SIZE` | 512 (résolution 0,98 Hz) — 1024 pour séparer BPFO/BPFI à très basse vitesse |
| `ENABLE_MIC`, `ENABLE_ENCODER`, `ENABLE_INA219` | Selon le matériel réellement monté |
| `ENABLE_BENCH_TEST` | `1` autorise l'injection de défauts de test par MQTT |
| `EDGE_*` | Seuils du pré-diagnostic embarqué, alignés sur `data/seed.json` |
| `NOMINAL_CURRENT_A`, `BELT_MM_PER_REV` | Caractéristiques mécaniques du banc |

Les **seuils et valeurs de calibration de référence** sont centralisés dans `data/seed.json`
(sections `thresholds`, `line`, `motors`) : c'est le document à mettre à jour quand le banc évolue.

---

## 5. Mise en service et calibration

1. **Détection des capteurs** : au démarrage, vérifier le moniteur série. Trois sondes DS18B20
   attendues (corps, pallier, ambiant) ; si une sonde manque, le backend bascule automatiquement
   sur l'estimation thermique (mode dégradé, signalé dans le heartbeat).
2. **Zéro de courant** : la carte mesure automatiquement l'offset ACS712 à l'arrêt pendant 2 s
   au démarrage. Pour le refaire à chaud : `POST /api/devices/esp32-conveyor-01/command`
   `{"action":"recalibrate"}` — la carte met la ligne à l'arrêt, recalibre, puis reprend.
3. **Offset accéléromètre** : même commande ; la carte mesure 200 échantillons au repos et
   mémorise les offsets ax/ay puis retire 1 g sur l'axe vertical.
4. **Vérification du zéro mécanique** : à l'arrêt, `vibration.rms` doit rester < 0,02 g et
   `kurtosis` ≈ 3. Sinon, vérifier la rigidité du support et l'éloignement des câbles de puissance.
5. **Montée en vitesse** : lancer la ligne à 60 rpm, puis 120, puis 150 ; vérifier que
   `speed.rpm` suit la consigne et que `steps_per_s` correspond à `rpm/60 × 200 × 16`.
6. **Relevé de la baseline** : laisser tourner 30 minutes à vide puis 30 minutes en charge
   nominale, relever `conveyor/{m}/features`, et mettre à jour `BASELINE` dans
   `backend/ml/features.py` (voir `docs/ML_PIPELINE.md` § 7).
7. **Test de bout en bout** : injecter un déséquilibre via `/simulator` (ou par MQTT), vérifier
   la remontée d'alerte, la prescription et l'ordre de travail.

---

## 6. Mise à jour OTA

Le firmware prépare la mise à jour (`ota_update`) et publie un accusé de réception. Pour la
finaliser, brancher un serveur HTTP et activer `Update.h` :

```cpp
#include <Update.h>
#include <HTTPClient.h>

void performOtaUpdate(const char* url, const char* expectedVersion) {
  HTTPClient http;
  http.begin(url);
  int code = http.GET();
  if (code != 200) { Serial.printf("[ota] HTTP %d\n", code); return; }
  int size = http.getSize();
  if (!Update.begin(size)) { Serial.println("[ota] place insuffisante"); return; }
  WiFiClient* stream = http.getStreamPtr();
  size_t written = Update.writeStream(*stream);
  if (written != (size_t)size || !Update.end(true)) {
    Serial.printf("[ota] échec (%s)\n", Update.errorString());
    return;
  }
  Serial.printf("[ota] succès — version %s, redémarrage\n", expectedVersion);
  ESP.restart();
}
```

Bonnes pratiques : servir l'image sur le réseau interne, comparer l'empreinte SHA-256 avant
d'écrire, conserver l'ancienne partition en secours (les partitions OTA de l'ESP32 permettent un
retour arrière automatique en cas d'échec de démarrage), et ne jamais déclencher une OTA sur une
ligne en production sans arrêt planifié.

---

## 7. Sécurité MQTT et du device

| Mesure | Mise en œuvre |
|---|---|
| Authentification | Comptes Mosquitto (jamais d'accès anonyme) ; un compte par device |
| Autorisation | `mosquitto/acl` : le device publie ses topics et ne reçoit que ses commandes |
| Chiffrement | `mosquitto/certs/generate-certs.sh` génère CA + serveur + device ; activer le listener 8883 et `MQTT_USE_TLS 1` |
| Rotation de clés | `POST /api/devices/{id}/command` `{"action":"rotate_key"}` → événement de sécurité + nouveau jeton |
| Détection de sabotage | Drapeau `tamper` dans le heartbeat (ouverture du boîtier / modification de la config NVS) |
| Journalisation | `security_event` en base : connexions, rotations, tentatives d'accès, OTA |
| Cloisonnement réseau | VLAN IoT dédié, broker non exposé sur Internet (ou via VPN/reverse proxy) |
| Mise à jour | OTA signée, canal `stable` par défaut, rollback automatique possible |

---

## 8. Adaptation des modèles au banc réel

1. Relever un jeu étiqueté (chaque défaut injecté séparément, 10-15 minutes par condition).
2. Mettre à jour `BASELINE` et `TOLERANCE` (`backend/ml/features.py`) avec les valeurs mesurées :
   ces constantes servent aux z-scores, au radar et au moteur heuristique de secours.
3. Ré-entraîner : `python -m ml.train --samples 200 --seed <nouvelle graine>` ou envelopper vos
   propres modèles (`docs/ML_PIPELINE.md` § 7).
4. Recalibrer la porte d'anomalie `ANOMALY_GATE` et le seuil `is_anomaly` sur les nouvelles
   distributions (sain vs défaut) puis relancer `python -m ml.validate`.
5. Ajuster les seuils de règles dans `data/seed.json` puis `python scripts/seed_db.py --apply`.
6. Documenter la nouvelle référence dans `data/seed.json → expected_results` : `seed_db.py --check`
   devient alors le test d'acceptation de l'installation.

---

## 9. Symptômes fréquents sur banc réel

| Symptôme | Cause probable | Action |
|---|---|---|
| `vibration.rms` anormalement élevée à l'arrêt | Support souple ou câbles de puissance proches | Rigidifier la platine, éloigner/torsader les câbles, refaire la calibration |
| Valeur de courant toujours nulle | Pont diviseur absent ou offset non calibré | Vérifier la tension à l'ADC (≈ 1,65 V à l'arrêt), relancer `recalibrate` |
| Une sonde DS18B20 à -127 °C | Sonde débranchée ou adresse ROM absente | Vérifier le bus 1-Wire et la résistance de tirage, remplacer la sonde |
| Redémarrages aléatoires | Chute de tension sur le 5 V pendant les commutations | Alimentation séparée + condensateurs, jamais de moteur sur l'USB |
| `steps_per_s` diverge de la consigne | Surcharge mécanique ou driver limité en courant | Ajuster le potentiomètre du driver, vérifier la tension d'alimentation |
| Le backend ne voit pas la carte | Broker, identifiants ou ACL | `mosquitto_sub -u conveyor-monitor -P … -t 'conveyor/#' -v` |
| Horodatage incohérent | NTP inaccessible | Le firmware bascule en temps monotone : autoriser l'accès NTP ou synchroniser le serveur |
