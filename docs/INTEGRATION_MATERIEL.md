# Intégration du matériel réel — du câblage à l'interface live

Ce document répond à une seule question : **une fois l'ESP32 câblé et flashé, comment les
mesures arrivent-elles dans l'interface, et comment je le vérifie étape par étape ?**

Il complète [`HARDWARE.md`](HARDWARE.md) (câblage, calibration, OTA, sécurité) en décrivant
la **chaîne logicielle** : chaque topic MQTT, quel élément de l'écran il alimente, quel
composant du backend le traite, et comment isoler une panne quand une valeur n'arrive pas.

---

## 1. La chaîne complète, en une image

```
                 ┌──────────────────────────── ESP32 (firmware/esp32_conveyor.ino) ─────────┐
                 │  MPU6050 · ACS712 · DS18B20 · micro · encodeur  →  filtres            │
                 │  calcul embarqué : RMS, kurtosis, FFT, features (21), pré-diagnostic  │
                 └───────────────┬───────────────────────────────────────────────────────┘
                                 │  Wi-Fi (TCP/IP) + MQTT  [TLS 8883 en production]
                                 ▼
        ┌────────────────────────────────────────────────────────────────────────────┐
        │  BROKER MQTT — Mosquitto (mosquitto/mosquitto.conf + ACL + comptes)        │
        │  topics : conveyor/{m1|m2}/telemetry · …/features · …/edge_inference      │
        │           conveyor/status · conveyor/{device}/heartbeat                   │
        └────────────────────────────┬───────────────────────────────────────────────┘
                                     │  abonnement du backend (5 familles de topics)
                                     ▼
   ┌──────────────────────── BACKEND FastAPI (MODE=HARDWARE) ───────────────────────────┐
   │ 1. services/mqtt_client.py   réception, compteurs, reconnexion automatique          │
   │ 2. services/ingestion.py     validation Pydantic → buffers mémoire → SQLite         │
   │ 3. ML (ml_service)           features → Isolation Forest → RandomForest → RUL        │
   │                              → Health Index → XAI                                     │
   │ 4. alerting.py               seuils + règles + anti-flap → alerte + prescription    │
   │ 5. realtime.py (hub)         diffusion instantanée par WebSocket                     │
   │ 6. API REST + rapports PDF   lecture historique, exports, ordres de travail          │
   └────────────────────────────┬───────────────────────────────────────────────────────┘
                                │  WebSocket /ws/telemetry  (JSON, un message par événement)
                                ▼
   ┌──────────────────────────── INTERFACE Next.js ────────────────────────────────────┐
   │  store Zustand (10 fps) → jumeau 3D, jauges, courbes canvas, alertes, KPI,         │
   │  fiche moteur, page équipements (RSSI/latence depuis le heartbeat)                 │
   └───────────────────────────────────────────────────────────────────────────────────┘
```

**Rien n'est « simulé » dans ce chemin** : la même ingestion traite une trame venue de
`scripts/esp32_simulator.py` (banc de test) ou d'une carte réelle. Seule la **source** change,
et elle est indiquée dans l'interface (les cartes envoyées par MQTT portent `source="mqtt"`).

---

## 2. Qui affiche quoi : correspondance topic ↔ écran

| Topic MQTT | Cadence | Traitement backend | Ce que vous voyez dans l'interface |
|---|---|---|---|
| `conveyor/m1/telemetry` <br> `conveyor/m2/telemetry` | 5-10 Hz | `ingestion.ingest_telemetry` → buffer 2048 pts, persistance throttlée, inférence IA | Courbes temps réel 60 s (vibrations, courant, température, vitesse, acoustique), jauge de santé, jauge RUL, valeurs instantanées des cartes moteur, jumeau 3D (vitesse de bande + couleur santé) |
| `conveyor/m1/features` <br> `conveyor/m2/features` | 1 Hz | `ingestion.ingest_features` → table `features` | Onglet **Analyse fréquentielle** et **Historique** de la fiche moteur, radar « signature vs baseline », corrélations de la page analytique |
| `conveyor/m1/edge_inference` <br> `conveyor/m2/edge_inference` | 1 Hz | `ingestion.ingest_edge_inference` → comparaison avec le modèle serveur | Panneau **Comparaison Edge (TinyML) vs Serveur** : accord des classes, latence embarquée (ms) vs serveur (ms) |
| `conveyor/status` | 5 Hz | `ingestion.ingest_status` → `hub.status` | Bandeau **État de la ligne** (EN MARCHE / ARRÊT), vitesse de bande en mm/s, charge, colis/min, état dans le jumeau 3D |
| `conveyor/esp32-conveyor-01/heartbeat` | 1 Hz | `ingestion.ingest_heartbeat` → table `device` | Page **Équipements** : voyant ONLINE/DEGRADED/OFFLINE, RSSI (dBm), latence, pertes, uptime, firmware, mémoire libre, état des défauts par moteur |
| `conveyor/cmd/{m1\|m2\|all}/{command}` | à la demande | `routers/simulator.py` publie (MODE=HARDWARE) | Boutons de la page **Simulateur** (défauts, vitesse, charge) et page **Équipements** (reboot, recalibrate, OTA, sampling, test réseau) |
| `conveyor/{device}/command_ack` | à la demande | `ingestion.ingest_ack` | Journal des commandes + notifications de la page Équipements |
| `conveyor/{device}/network_test` | à la demande | `ingestion` | Bloc **Diagnostic réseau** (latence, débit, pertes) |

> **Le WebSocket ne porte pas les topics bruts** : le backend transforme chaque trame en
> message typé (`telemetry`, `inference`, `edge_inference`, `alert`, `status`, `heartbeat`,
> `device_command`, `rates`, `summary`). Le navigateur n'a donc jamais besoin de connaître
> l'IP du broker — il n'appelle que `/api/*` et `/ws/*` en relatif, proxifiés par Next.js.

---

## 3. La journée du câblage : procédure à suivre

### Étape 0 — Démarrer la pile logicielle (5 min)

```bash
# 1) Broker MQTT (avec Docker) — génère les comptes au premier démarrage
docker compose up -d mosquitto
docker compose logs mosquitto | grep -i "compte\|password"   # identifiants créés

# 2) Backend en mode matériel
cd backend
cp .env.example .env
#   MODE=HARDWARE
#   MQTT_ENABLED=true
#   MQTT_HOST=<IP de la machine qui héberge Mosquitto>   (pas "localhost" si broker distant)
#   MQTT_USERNAME=conveyor-backend
#   MQTT_PASSWORD=<celui affiché par les journaux de mosquitto>
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3) Interface
cd ../frontend && npm run build && npm run start        # → http://localhost:3000
```

Sans Docker (broker de test livré) :

```bash
backend/.venv/bin/pip install amqtt==0.11.3
backend/.venv/bin/python tools/mqtt_broker_test.py      # broker d'essai sur :1883
```

### Étape 1 — Vérifier le broker AVANT de câbler l'ESP32

```bash
# Écouter tous les topics du banc (le compte de supervision existe par défaut)
mosquitto_sub -h localhost -p 1883 -u conveyor-monitor -P <mot_de_passe> -t 'conveyor/#' -v
```

Vous devez voir… rien (normal : aucun device ne publie encore). C'est le point de départ
du diagnostic : si cette commande échoue, le problème est côté broker et non côté carte.

### Étape 2 — Flasher la carte et lire le moniteur série

```bash
cd firmware
pio run -t upload && pio device monitor -b 115200
```

Attendu au démarrage (firmware `esp32_conveyor.ino`) :

```
[net] Wi-Fi connecté — IP 192.168.1.42 (RSSI -58 dBm)
[net] NTP synchronisé
[sensors] MPU6050 détecté (0x68) · 3 × DS18B20 · ACS712 M1/M2 calibrés
[mqtt] connecté à 192.168.1.10:1883 (TLS=0, QoS=1)
[mqtt] abonnements : conveyor/cmd/#, conveyor/esp32-conveyor-01/config/#
[pub] heartbeat 1 Hz · telemetry 8 Hz · features 1 Hz · edge_inference 1 Hz · status 5 Hz
```

Chaque ligne correspond à un maillon de la chaîne : Wi-Fi → capteurs → MQTT → cadences de
publication. La première ligne en échec désigne le maillon à corriger (voir § 6).

### Étape 3 — Vérifier les topics un par un

```bash
# Télémétrie (doit afficher ~8 messages/s)
mosquitto_sub -h localhost -u conveyor-monitor -P <mdp> -t 'conveyor/m1/telemetry' -v | head -3

# Heartbeat (1/s : c'est lui qui allume le voyant ONLINE dans la page Équipements)
mosquitto_sub -h localhost -u conveyor-monitor -P <mdp> -t 'conveyor/+/heartbeat' -v | head -2

# Inférence embarquée (1/s : c'est elle qui alimente « Edge vs Serveur »)
mosquitto_sub -h localhost -u conveyor-monitor -P <mdp> -t 'conveyor/m1/edge_inference' -v | head -2
```

### Étape 4 — Vérifier le backend

```bash
curl -s localhost:8000/api/overview | python -m json.tool | head -40
#   "mqtt": {"enabled": true, "status": "ONLINE", "messages": 1234, ...}
curl -s localhost:8000/api/devices | python -m json.tool | head -30
```

| Ce que vous lisez | Interprétation |
|---|---|
| `mqtt.status = ONLINE` et `messages` qui augmente | Le backend reçoit bien les trames |
| `messages` fige mais la carte publie | ACL MQTT : le compte du backend n'est pas abonné aux bons topics |
| `status = RECONNECTING` | Mauvais hôte/port/identifiants, ou broker arrêté |
| `device.status = ONLINE` + `last_heartbeat` récent | La supervision voit la carte |

### Étape 5 — Vérifier l'interface

1. Ouvrez `http://localhost:3000` : l'en-tête doit afficher la pastille verte **LIVE**,
   la latence WebSocket, et **plusieurs messages/s** (`msg/s`).
2. Les KPI doivent quitter les gabarits et afficher des valeurs réelles (la première
   inférence prend ~1 s après la première trame, une fois la fenêtre d'analyse remplie).
3. Tournez la poulie à la main : la courbe de vibration et la vitesse de bande doivent
   réagir en moins d'une seconde (c'est la preuve visuelle que la boucle est fermée).
4. Page **Équipements** : la passerelle doit apparaître avec son RSSI et son uptime réels.
5. Page **Simulateur** : appuyez sur un défaut → le firmware le reçoit sur
   `conveyor/cmd/all/inject_fault`, puis la télémétrie change et une alerte se crée,
   **côté interface, sur le matériel réel**.

> **À ce stade, la carte réelle et le banc simulé sont interchangeables** : le reste de
> l'application (IA, alertes, PDF, historique, ordres de travail) fonctionne à l'identique.
> C'est exactement ce que fait `scripts/esp32_simulator.py`.

---

## 4. Les trois modes, et quand les utiliser

| `MODE` | Simulateur interne | Pont MQTT | Usage |
|---|---|---|---|
| `SIMULATION` (défaut) | **actif** (8 Hz, M1+M2) | optionnel | Démonstration sans matériel, salle de contrôle immédiate |
| `HARDWARE` | **coupé** (`SIM_AUTOSTART=false`) | **obligatoire** | Banc réel : la carte est la seule source de données |
| `HYBRID` | actif | actif | Atelier : un moteur instrumenté + un moteur virtuel, ou bascule progressive |

Le mode se change **sans redémarrer** pour la partie visible (les routes `/api/simulator/*`
relaient alors les commandes en MQTT au lieu d'agir sur la physique interne) ; le basculement
définitif se fait dans `backend/.env` puis au redémarrage du service.

---

## 5. Ce qui change vraiment le jour du câblage (à savoir)

| Sujet | En simulation | Sur banc réel | Ce qu'il faut faire |
|---|---|---|---|
| **Baseline** | valeurs de synthèse | vos vibrations/courants réels | Relever 30 min à vide + 30 min en charge, puis mettre à jour `BASELINE` dans `backend/ml/features.py` (voir `ML_PIPELINE.md` § 7) |
| **Seuils de règles** | 0,35 g d'avertissement, 65 °C… | dépend du montage | Ajuster dans `data/seed.json` → `python scripts/seed_db.py --apply` |
| **Valeurs de référence** | `expected_results` du banc simulé | vos valeurs mesurées | Réécrire `expected_results` : `seed_db.py --check` devient votre test d'acceptation |
| **Glissement de bande** | modèle physique | exige l'**encodeur** | Sans encodeur, `belt_speed_error` est publié à 0 (le glissement reste visible par le courant) |
| **Acoustique** | estimée | exige le **micro INMP441** | `ENABLE_MIC 0` si absent : la fonctionnalité disparaît proprement |
| **Horodatage** | horloge du serveur | horloge de la carte (NTP) | Autoriser l'accès NTP, sinon la carte bascule en temps monotone et les timestamps sont recalés côté backend |
| **Latence WebSocket** | ~2-15 ms | identique (chemin local) | Rien à régler ; surveiller `latence` dans l'en-tête |
| **Robustesse réseau** | sans objet | coupures Wi-Fi | La carte conserve 1 h de trames en mémoire et republie à la reconnexion ; le backend bufferise et signale `device_offline` après 45 s sans heartbeat |

---

## 6. Arbre de diagnostic

```
La page reste vide ?
│
├─ En-tête : badge OFFLINE / RECONNEXION
│   → WebSocket : le backend tourne-t-il ? `curl localhost:8000/api/health`
│   → Proxy : le frontend a-t-il été reconstruit après un changement de port ?
│
├─ Badge LIVE mais « 0 msg/s »
│   → /api/overview → mqtt.status ?
│      ├─ RECONNECTING : hôte/port/identifiants du broker
│      └─ ONLINE : la carte ne publie pas → moniteur série de l'ESP32
│
├─ msg/s > 0 mais les jauges restent vides
│   → La fenêtre d'analyse est en cours de remplissage (attendre ~2 s)
│   → Sinon : validation Pydantic rejetée → voir les journaux backend (`payload invalide`)
│     comparer la trame publiée avec le schéma de `backend/app/schemas.py`
│
├─ Une seule valeur est absente (ex. température)
│   → Capteur concerné : moniteur série (`[sensors] DS18B20 × 2 seulement`)
│   → Le backend bascule sur l'estimation et le signale dans le heartbeat
│
├─ Valeurs absurdes (courant nul, vibration énorme)
│   → `POST /api/devices/esp32-conveyor-01/command` {"action":"recalibrate"}
│   → Câblage : masse commune, éloignement des câbles de puissance, condensateurs
│
└─ Alerte « passerelle hors ligne » alors que la carte publie
    → Vérifier `conveyor/+/heartbeat` : le firmware doit publier 1 Hz
    → Vérifier l'ACL : le compte du device doit pouvoir publier `conveyor/{device}/heartbeat`
```

---

## 7. Passage du banc simulé au banc réel, sans coupure de service

```bash
# 1) Le banc simulé remplit l'interface (déjà en marche)
backend/.venv/bin/python scripts/esp32_simulator.py run --host <broker>

# 2) Nouvelle fenêtre : on lance la carte réelle sur les MÊMES topics
#    → les deux sources publient en parallèle pendant la mise au point

# 3) On coupe le banc simulé : la carte prend la main, sans redémarrer le backend
#    (Ctrl+C dans la fenêtre du simulateur)

# 4) On valide la stabilité
curl -s localhost:8000/api/system/health | python -m json.tool
python scripts/seed_db.py --check        # après avoir mis à jour expected_results
```

Cette méthode évite tout « trou » dans l'historique et permet de comparer moteur virtuel et
moteur réel côte à côte — la page **Analytique** dispose pour cela du comparateur M1/M2.

---

## 8. Vérification automatique de ce dépôt (référence)

La chaîne ci-dessus a été exécutée de bout en bout dans cet environnement :

```
Broker MQTT de test (tools/mqtt_broker_test.py)          → ONLINE sur 0.0.0.0:1883
Backend MODE=HARDWARE (pont MQTT)                        → 1940+ messages ingérés, 0 perte
Banc ESP32 simulé (scripts/esp32_simulator.py, 8 Hz)      → 2 moteurs, 5 familles de topics
Interface (Next.js, mode production)                      → badge LIVE, 16 msg/s, 29 ko/s
Page Équipements                                         → passerelle ONLINE, RSSI −58 dBm, latence 11,4 ms
Commande interface → carte (inject_fault)                 → relayée sur conveyor/cmd/M1/inject_fault
                                                           → diagnostic BEARING_FAULT (confiance 79 %)
                                                           → ordre de travail « Remplacer le roulement »
Effacement (clear_fault)                                  → retour à HEALTHY, RUL ≈ 2900-3400 h
```

Reproduction :

```bash
backend/.venv/bin/python tools/mqtt_broker_test.py &                     # broker
cd backend && MODE=HARDWARE MQTT_ENABLED=true MQTT_HOST=127.0.0.1 \
  .venv/bin/python -m uvicorn app.main:app --port 8000 &
.venv/bin/python ../scripts/esp32_simulator.py run --host 127.0.0.1      # « carte »
cd ../frontend && npm run smoke                                          # 8 pages + captures
```

---

## 9. Après le câblage : étalonner les modèles sur VOS mesures

1. **Collecter** : chaque défaut injecté séparément, 10-15 min par condition, en gardant
   l'étiquette (un fichier par condition). Le banc peut publier sans interruption.
2. **Comparer** : page **Analytique** (corrélations) et **MLOps** (dérive entraînement ↔
   production) pour repérer les features qui s'écartent du jeu synthétique.
3. **Ré-entraîner** : `python -m ml.train --samples 200 --seed <graine>` ou brancher vos
   propres modèles (`ML_PIPELINE.md` § 7).
4. **Valider** : `python -m ml.validate --artifacts ./artifacts` puis
   `python scripts/seed_db.py --drill` (les plages attendues sont dans `data/seed.json`).
5. **Figer** : mettre à jour `expected_results`, archiver l'artefact, noter la version dans
   `models` — la page **MLOps** permet de revenir en arrière en un clic.

---

## 10. Ce que l'interface montre « en direct » sur banc réel (repères)

| Élément d'écran | Origine exacte | Fréquence de rafraîchissement |
|---|---|---|
| Jauge de santé M1/M2 | `health_index` de l'inférence serveur (fusion anomalie + classe + thermique + usure) | à chaque trame (8 Hz), lissée par EMA |
| RUL + bande de confiance | régression RUL du modèle, médiane glissante | 1 Hz |
| Courbes canvas | télémétrie brute | 10 fps (limité côté client) |
| Jumeau 3D : vitesse de bande | `speed.belt_mm_s` de `conveyor/status` | 5 Hz |
| Jumeau 3D : couleur des moteurs | `health_index` | 8 Hz |
| Fil d'alertes | moteur de règles + diagnostic IA | à l'événement (< 1 s) |
| Page Équipements | heartbeat (RSSI, uptime, heap, firmware) | 1 Hz |
| Edge vs Serveur | `edge_inference` embarquée vs inférence serveur | 1 Hz |
| Spectre FFT / waterfall | `fft_peaks` de la télémétrie + features 1 Hz | 1 Hz |
