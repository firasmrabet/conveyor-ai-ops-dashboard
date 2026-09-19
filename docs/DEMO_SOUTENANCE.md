# Démonstration de soutenance — Conveyor AI Ops

Objectif : en **3 minutes**, montrer une chaîne complète — acquisition, transport, IA, alerting,
prescription, intervention, rapport — puis répondre aux questions techniques.

---

## 1. Préparation (5 minutes avant)

```bash
# 1. Stack complète, base fraîche, modèle validé
docker compose down -v && docker compose up --build -d

# 2. Contrôle des repères
curl -s localhost:8000/api/health | python -m json.tool | head -20   # status ok, mode SIMULATION
curl -s localhost:8000/api/motors | python -m json.tool | head -20   # M1/M2 HEALTHY, santé > 96
python scripts/seed_db.py --check                                    # conforme aux valeurs de référence

# 3. Vérification du flux MQTT
python scripts/esp32_simulator.py monitor --interval 3               # ~20 msg/s, 5 topics actifs
```

À garder sous les yeux :

* onglet `/` (Control Room) — la pastille **MQTT LIVE** doit être verte et pulsante ;
* onglet `/simulator` — pour lancer la démo ;
* onglet `/alerts` — pour montrer la boucle de maintenance ;
* onglet `/motors/M1` — pour le spectre, le spectrogramme et le XAI ;
* dossier des rapports PDF prêts à être téléchargés.

---

## 2. Déroulé de la démonstration

| Temps | Action à l'écran | Discours / points à marquer |
|---|---|---|
| 0:00 | `/` → Control Room | « Deux moteurs pas à pas, capteurs vibration/courant/température, tout remonte par MQTT à 8 Hz. Le jumeau 3D reprend la vitesse réelle de la bande. » |
| 0:20 | Survolez les KPI | « Santé de flotte, disponibilité, RUL minimale, latence MQTT — calculés en continu. » |
| 0:35 | `/simulator` → **Démo 3 minutes** | « Le scénario est le même dans l'interface, dans le backend et dans le firmware : on peut rejouer la démo sans ordinateur. » |
| 0:50 | Retour à `/` | À 0:40 le déséquilibre M1 est aggravé : « la vibration RMS dépasse le seuil, la jauge descend, la courbe change de couleur ». |
| 1:05 | Cliquez sur le moteur M1 en 3D → `/motors/M1` | « Classe UNBALANCE, 81 % de confiance, taux harmonique 1× à 6,8 écarts-types, RUL ≈ 200-300 h avec bande de confiance. » |
| 1:25 | Spectre + waterfall | « Les marqueurs BPFO/BPFI encadrent les fréquences de roulement : c'est ce que regarde le mainteneur. » |
| 1:40 | Panneau XAI | « L'explication est générée : vibration globale, harmonique 1×, courant — l'alerte est défendable. » |
| 1:55 | `/alerts` | « Alerte double : seuil (RULES) et diagnostic IA (ML). Prescription automatique : “Rééquilibrer la poulie”, priorité MOYENNE, 7 jours. Ordre de travail créé. » |
| 2:10 | Acquitter, assigner, commenter | « Boucle fermée : acquittement, assignation, ordre de travail, journal d'interventions signé. » |
| 2:25 | `/analytics` | « MTBF, MTTR, faux positifs, comparateur M1/M2, corrélation température/courant, heatmap des dérives. » |
| 2:35 | `/ml` | « Isolation Forest + RandomForest + RUL : exactitude 0,9125, F1 0,9136, ROC AUC 0,9712. Comparaison edge vs serveur, dérive, rollback en un clic. » |
| 2:50 | Génération du **rapport PDF** | « Rapport signé : KPI, diagnostic, RUL, spectre, historique, plan d'action. Prêt pour le service maintenance. » |
| 3:00 | `/devices` | « Passerelle ESP32 : latence, RSSI, TLS, rotation de clés, journal de sabotage. » |

Variante « matériel réel » : remplacer l'étape `/simulator` par l'injection MQTT
(`scripts/esp32_simulator.py fault --motor M1 --fault UNBALANCE --intensity 85`) en gardant le
backend en `MODE=HARDWARE` : la chaîne démontrée est alors strictement celle du terrain.

---

## 3. Chiffres à citer (vérifiés en exécution)

| Indicateur | Valeur |
|---|---|
| Modèle actif | `conveyor-synth v1.4.0` (Isolation Forest + RandomForest + régression RUL) |
| Exactitude / F1 / ROC AUC | 0,9125 / 0,9136 / 0,9712 |
| Erreur RUL (MAE) | 338 h sur un horizon de 6 000 h |
| Stabilité du RUL (banc nominal) | écart-type 41 h |
| Banc sain | santé 96-99, anomalie 0,00-0,25, RUL ≈ 3 300-3 500 h |
| Déséquilibre M1 à 85 % | santé 54,7-65,1, anomalie 0,54-0,60, RUL 209-305 h |
| Détection de défaut de roulement (auto-test) | santé 62,9, anomalie 0,480, RUL 667 h |
| Latence bout en bout (capteur → écran) | 250-400 ms |
| Débit MQTT | ~20 msg/s, ~28 Ko/s |
| Latence d'inférence serveur | ~42 ms (edge TinyML : 1,8 ms) |
| Anti-flapping | 3 confirmations + 90-120 s de temporisation |

---

## 4. Tests d'acceptation (à exécuter avant la soutenance)

```bash
# 1. IA : 7 scénarios de défaut + sain
cd backend && python -m ml.inference --selftest            # attendu : 7/7
python -m ml.validate --artifacts ./artifacts              # attendu : code 0

# 2. Base et références
cd .. && python scripts/seed_db.py --check                 # attendu : CONFORME

# 3. Injections et alertes (mode SIMULATION)
curl -X POST localhost:8000/api/simulator/clear-faults
sleep 30 && curl -s localhost:8000/api/motors | python -c \
 "import sys,json; [print(m['id'], m['fault_class'], m['health_index']) for m in json.load(sys.stdin)]"
curl -X POST localhost:8000/api/simulator/inject-fault -H 'Content-Type: application/json' \
     -d '{"motor_id":"M1","fault":"UNBALANCE","intensity":85}'
sleep 45 && curl -s 'localhost:8000/api/alerts?limit=5' | python -m json.tool | head -30
#   attendu : alerte THRESHOLD (vibration_rms) + alerte AI_CLASS (fault_confidence, UNBALANCE)

# 4. Chaîne MQTT complète (mode HARDWARE) — nécessite un broker
cd /tmp && python3 -m amqtt ... (ou docker compose --profile edge up)
MQTT_HOST=127.0.0.1 MODE=HARDWARE MQTT_ENABLED=true uvloop... # voir docs/TROUBLESHOOTING.md
curl -X POST localhost:8000/api/simulator/inject-fault -H 'Content-Type: application/json' \
     -d '{"motor_id":"M1","fault":"BEARING_FAULT","intensity":70}'
#   attendu : {"mode":"HARDWARE","available":false,"forwarded":{"topic":"conveyor/cmd/M1/inject_fault","published":true}}

# 5. Rapport PDF et exports
curl -o rapport-M1.pdf   localhost:8000/api/motors/M1/report
curl -o rapport-global.pdf -X POST localhost:8000/api/reports/generate \
     -H 'Content-Type: application/json' -d '{"motor_id":"M1","period_hours":24,"format":"pdf"}'
curl -o export.csv 'localhost:8000/api/analytics/export.csv?range=24h'
```

---

## 5. Questions fréquentes du jury

**« Vos modèles sont entraînés sur des données simulées — est-ce valable ? »**
Le jeu de données n'est pas statistique mais **physique** : un modèle mécanique (harmoniques
1×/2×/3×, impulsions de roulement périodiques, inertie thermique, usure cumulée) génère les
mesures ; l'IA apprend donc des signatures conformes à la mécanique réelle. Le projet est
explicitement conçu pour être ré-entraîné sur les relevés du banc réel (`docs/ML_PIPELINE.md` § 7),
et un moteur heuristique de secours garantit un diagnostic même sans artefact.

**« Que se passe-t-il si le réseau tombe ? »**
L'ESP32 continue d'acquérir, de calculer les caractéristiques et d'exécuter son pré-diagnostic
TinyML (et peut couper la ligne localement en cas de blocage moteur). Les trames sont perdues
pendant la coupure, mais la supervision détecte l'absence de heartbeat en 15 s et l'interface
signale l'équipement hors ligne.

**« Comment évitez-vous les fausses alertes ? »**
Quatre mécanismes cumulés : hystérésis sur les seuils, durée minimale de dépassement, anti-spam
par temporisation, et confirmation par N inférences concordantes (3 par défaut) avec lissage EMA
et verrouillage de classe sur 2 coups. Les faux positifs sont comptés dans `/analytics`.

**« Comment décidez-vous d'une intervention ? »**
L'alerte porte la classe de défaut, la confiance, les explications XAI, le RUL et sa bande de
confiance ; l'alerting associe une prescription (action, priorité, délai) et crée un ordre de
travail. Un humain valide : le système propose, l'opérateur dispose.

**« Pourquoi MQTT et pas HTTP ? »**
Publish/subscribe, faible surcharge (2 octets d'en-tête), QoS 1, dernière volonté pour détecter
les coupures, découplage producteur/consommateur : c'est le standard de l'IIoT, et cela permet à
plusieurs consommateurs (backend, supervision, passerelle SCADA) de lire le même flux.

**« Comment passe-t-on au banc réel ? »**
`MODE=HARDWARE` + firmware téléversé : aucun changement de code applicatif. Les endpoints de
simulation deviennent des relais de commandes MQTT, la validation Pydantic reste la même, et
l'interface ne voit pas la différence (`docs/HARDWARE.md`).

**« Et la cybersécurité ? »**
Comptes MQTT distincts, ACL par device, TLS optionnel avec CA locale, JWT + rôles côté API,
limitation de débit, CORS restreint, journal `security_event`, rotation de clés et détection de
sabotage, secrets hors du dépôt (`.env` non versionné, `.env.example` documenté).

---

## 6. Plan de secours

| Incident | Repli immédiat |
|---|---|
| Le broker ne répond pas | Passer en `MODE=SIMULATION` : toutes les pages restent démontrables. |
| L'IA charge mal l'artefact | Le moteur heuristique prend le relais automatiquement (7/7 en auto-test). |
| Le frontend ne démarre pas | Démontrer via `/docs` (Swagger) et `/api/overview` : le backend expose tout. |
| Le matériel réel casse | Utiliser `scripts/esp32_simulator.py` : le protocole MQTT est identique. |
| Question technique imprévue | `docs/` contient architecture, protocole, API, IA, matériel, déploiement et dépannage. |
