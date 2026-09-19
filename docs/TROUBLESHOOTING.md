# Dépannage — Conveyor AI Ops

Guide de diagnostic classé par symptôme, avec la commande exacte à exécuter. Les cas marqués
**[vécu]** ont réellement été rencontrés pendant la mise au point du projet et corrigés : ils
sont listés en premier, car ce sont les plus probables.

---

## 1. Démarrage et conteneurs

| Symptôme | Diagnostic | Correctif |
|---|---|---|
| `docker compose up` reste bloqué sur le backend | Le backend attend le broker (`depends_on: service_healthy`) | `docker compose logs mosquitto` : vérifier que `entrypoint.sh` a bien créé les comptes et que le mot de passe du volume correspond à `.env`. |
| `backend` redémarre en boucle | Artefact IA absent/instable au démarrage | `docker compose logs backend` → si `ml.validate` échoue, le script ré-entraîne ; en cas d'échec répété : `docker compose exec backend python -m ml.train --samples 150 --seed 7`. |
| `frontend` répond 500 | Backend non joignable au moment du rendu | `docker compose restart frontend` après que `backend` soit `healthy`. |
| Port déjà utilisé (`3000`, `8000`, `1883`) | Instance précédente encore active | `fuser -k 8000/tcp` (ou `lsof -i :8000`), puis relancer. **[vécu]** Un uvicorn orphelin garde le port et sert un **ancien modèle** : toujours vérifier la version dans `/api/health`. |
| `docker: command not found` | Docker non installé sur la machine de développement | Le projet reste exécutable en local (voir § 5) ; les fichiers Compose ne peuvent être que validés syntaxiquement (`python -c "import yaml;yaml.safe_load(open('docker-compose.yml'))"`). |

---

## 2. MQTT et flux de données

| Symptôme | Diagnostic | Correctif |
|---|---|---|
| En-tête : `MQTT : DISABLED` | `MQTT_ENABLED=false` | Passer à `true` puis redémarrer le backend (ou changer dans `/settings`). |
| En-tête : `MQTT : CONNECTING` en boucle | Identifiants ou hôte erronés | `docker compose exec mosquitto mosquitto_sub -u conveyor -P conveyor -t 'conveyor/#' -v`. |
| Aucun message reçu alors que le device publie | ACL trop restrictive | Le compte device doit pouvoir publier sur `conveyor/{son id}/#`, `conveyor/status` ; ajouter dans `mosquitto/acl` puis redémarrer le broker. |
| Payloads rejetés dans les journaux | Champ obligatoire manquant ou type invalide | `logger.warning("Payload de télémétrie rejeté …")` indique le champ fautif ; vérifier la conformité au schéma (`docs/MQTT_PROTOCOL.md` § 2.1). |
| `MQTT : ONLINE` mais courbes figées | Le backend reçoit des trames dont le `ts` est dans le futur/passé lointain (horloge non synchronisée) | Le firmware bascule en temps monotone si le NTP est inaccessible ; autoriser NTP ou synchroniser le serveur. |
| Un simulateur publie alors que le mode est `HARDWARE` | Processus `scripts/esp32_simulator.py` oublié en tâche de fond | `pkill -f esp32_simulator` puis vérifier avec `python scripts/esp32_simulator.py monitor`. |

---

## 3. Intelligence artificielle

| Symptôme | Diagnostic | Correctif |
|---|---|---|
| RUL qui saute de 1 100 à 3 400 h | Fenêtre d'historique trop courte : une pente calculée sur quelques minutes est bruitée | Corrigé dans `ml/model.py` (médiane du tiers initial vs final + facteur `clip(1 − 0,0015·vitesse, 0,45, 1)`). **[vécu]** Après correctif : σ = 41 h sur banc nominal. Si cela réapparaît, vérifier que ce code n'a pas été remplacé par une régression linéaire. |
| Un moteur sain est classé anormal | Décalage de vitesse propre au moteur non présent dans les données d'entraînement | Corrigé en ajoutant de la diversité de glissement dans `ml/synthetic.py` (45 % des échantillons sains) et en recalibrant la normalisation du score d'anomalie. **[vécu]** Contrôle : `python -m ml.inference --selftest` doit donner 7/7 et un état nominal à ~97. |
| Anomalie très élevée au démarrage | Premiers buffers remplis de bruit pur | Les buffers sont amorcés avec un signal nominal réel (`MotorPhysics.__post_init__`) ; si le problème revient, vérifier cette initialisation. **[vécu]** |
| Alertes IA en rafale | Anti-flapping désactivé | `AI_CONFIRMATIONS=3`, `AI_DEBOUNCE_S=90`, `ANOMALY_CONFIRMATIONS=3`, `ANOMALY_DEBOUNCE_S=120`. |
| `ml.validate` retourne 1 | Artefact incomplet ou entraîné sur un autre jeu de caractéristiques | Ré-entraîner : `python -m ml.train --samples 150 --out ./artifacts --version v1.4.0 --seed 7`. |
| Score d'anomalie proche de 0 pour tous les défauts | Le classement du score a été fait sur des données non standardisées | L'Isolation Forest attend des données **transformées par le scaler** : toujours passer par `model.score_samples(X_scaled)`. **[vécu]** Un seuil appliqué aux valeurs brutes donnait 0,68 pour tout. |
| Page `/ml` lente à s'ouvrir | Recalcul des courbes ROC/PR et de la dérive | Un cache de 30 s est appliqué (`_METRICS_CACHE`) ; si la première ouverture reste lente, c'est le calcul initial (acceptable). |

---

## 4. Frontend

| Symptôme | Diagnostic | Correctif |
|---|---|---|
| Erreur réseau dans la console | Backend arrêté ou proxy cassé | Vérifier `frontend/next.config.mjs` : `/api/:path*` et `/ws/:path*` doivent pointer vers `http://127.0.0.1:8000`. |
| Le WebSocket ne s'ouvre pas derrière un reverse proxy | En-têtes `Upgrade`/`Connection` absents | Ajouter le bloc `location /ws/` (voir `docs/DEPLOYMENT.md` § 4.1). |
| Graphique vide au premier chargement | Historique non encore amorcé | Les buffers sont préremplis au démarrage (`_seed_realtime_buffers`) ; si l'écran reste vide, vérifier que le backend a bien terminé son amorçage (`Seed terminé …` dans les journaux). |
| Courbes saccadées | Throttling client trop permissif ou trop de séries | Le rendu est limité à ~10 fps et les buffers à 300-600 points ; réduire le nombre de séries affichées sur la page si nécessaire. |
| Thème clair illisible | Contraste AA non respecté sur une couleur personnalisée | Revenir aux jetons du design system (cyan `#00E5FF`, vert `#00E396`, ambre `#FFB020`, rouge `#FF4D4F`, violet `#7C5CFF`). |
| `npm run build` échoue | Types stricts ou dépendance manquante | `npx tsc --noEmit` (doit être silencieux) puis `npm install` — le projet se compile en TypeScript strict. |

---

## 5. Exécution locale sans Docker

**Ordre de démarrage obligatoire** (chaque étape dépend de la précédente) :

```bash
# 1. Backend
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m ml.validate --artifacts ./artifacts || python -m ml.train --samples 150 --seed 7
uvicorn app.main:app --port 8000

# 2. Brocker de test optionnel (si l'on veut tester MQTT sans Mosquitto)
python3 -m pip install --user amqtt paho-mqtt
#   (sur une machine sans droits root, amqtt fait très bien l'affaire pour un banc local)

# 3. Frontend
cd ../frontend && npm install && npm run dev
```

Repères de bon fonctionnement : `Seed terminé` dans les journaux, `Moteur IA actif :
sklearn-bundle v1.4.0`, `Artefact chargé (accuracy=0.9125 …)`, `9 règles d'alerte actives`,
`APScheduler démarré : 7 tâches`, puis `Backend prêt — documentation interactive sur /docs`.

| Erreur locale | Cause | Correctif |
|---|---|---|
| `ModuleNotFoundError: No module named 'app'` | Exécution depuis un autre dossier | Toujours lancer uvicorn **depuis `backend/`** (les modules `app.*` et `ml.*` sont résolus à la racine du dossier). |
| `ModuleNotFoundError: No module named 'paho'` | Le script MQTT est exécuté avec un interpréteur sans paho | Utiliser l'interpréteur du projet : `backend/.venv/bin/python scripts/esp32_simulator.py …`. **[vécu]** |
| `Object of type FaultState is not JSON serializable` | Le champ `fault` de la physique est un objet, pas une chaîne | Le script utilise `_fault_label()` pour extraire le nom du défaut. **[vécu]** |
| `Callback API version 1 is deprecated` | paho-mqtt 2.x | Le script détecte la version (`CallbackAPIVersion.VERSION2`) et reste compatible 1.x. **[vécu]** |
| `AttributeError: 'str' object has no attribute 'listeners'` | API amqtt différente selon la version | Utiliser un dict passé à `BrokerConfig.from_dict(...)` (le fichier YAML seul n'est plus accepté par `Broker`). **[vécu]** |
| `database is locked` | Base SQLite supprimée ou processus concurrent pendant l'écriture | Arrêter le serveur avant de manipuler les fichiers ; sinon `rm data/conveyor.db*` puis redémarrer (ré-amorçage automatique). |

---

## 6. Rapports, exports et API

| Symptôme | Diagnostic | Correctif |
|---|---|---|
| `405 Method Not Allowed` sur `GET /api/reports/generate` | La génération est un `POST` | Utiliser `POST /api/reports/generate` ; pour un simple téléchargement : `GET /api/motors/{id}/report`. **[vécu]** |
| PDF vide ou incomplet | Aucune donnée sur la période demandée | Élargir `period_hours` (24 → 168) ou vérifier que la télémétrie est bien persistée (`GET /api/motors/M1/telemetry`). |
| `429 Too Many Requests` | Limitation de débit atteinte (rafales de l'interface) | Ajuster `RATE_LIMIT_PER_MINUTE` (600/min par défaut). |
| `401 Unauthorized` | Jeton expiré (12 h par défaut) | Se reconnecter sur `/api/auth/login` ; ajuster `ACCESS_TOKEN_EXPIRE_MINUTES` si besoin. |
| `422 Unprocessable Entity` | Corps de requête non conforme | Le détail renvoyé indique le champ fautif ; comparer au schéma dans `/docs`. |
| Export CSV énorme | Rétention longue et cadence élevée | Filtrer par période (`from`/`to`) ou réduire `RAW_RETENTION_DAYS`. |

---

## 7. Diagnostic rapide en une commande

```bash
# Vue d'ensemble : santé backend, modèle, MQTT, simulation, KPI de flotte
curl -s localhost:8000/api/health | python -m json.tool
curl -s localhost:8000/api/overview | python -m json.tool | head -40

# Flux MQTT : débit par topic et dernier payload
python scripts/esp32_simulator.py monitor --interval 5

# Validation dynamique : injecte 3 défauts et vérifie la réaction + la récupération
python scripts/seed_db.py --drill

# Tests unitaires (hystérésis des règles d'alerte, disponibilité pondérée)
python backend/tests/test_alert_rules.py     # venv du backend activé

# Test navigateur : 8 pages, onglets, badge LIVE, captures dans docs/screenshots/
cd frontend && npm run smoke && cd ..

# Références de l'installation (modèle, moteurs, seuils)
python scripts/seed_db.py --check

# Auto-test IA complet
cd backend && python -m ml.inference --selftest && python -m ml.validate --artifacts ./artifacts

# Journaux des conteneurs
docker compose logs --since=10m | grep -Ei "erreur|error|alerte|severity"
```

---

## 8. Journal des correctifs de mise au point

| Correctif | Origine | Effet |
|---|---|---|
| Anti-bruit du RUL (médiane + facteur de dégradation) | RUL instable sur fenêtre courte | σ de 41 h au lieu de milliers d'heures d'oscillation |
| Diversité du glissement dans les données saines | Moteur M2 classé anormalement en production | Classe HEALTHY stable sur les deux moteurs |
| Recalibrage de la normalisation du score d'anomalie | Défauts réels noyés dans la zone « normale » | Sensibilité correcte : sain 0,00-0,35 ; défaut 0,48-0,65 |
| Initialisation des buffers avec un signal nominal | Fausses alertes durant la première seconde | Démarrage propre, aucune alerte fantôme |
| Standardisation avant l'Isolation Forest | Score d'anomalie incohérent | Suppression de la classe de bugs « score identique partout » |
| Verrouillage de classe 2 coups + EMA | Étiquette de diagnostic clignotante | Lecture stable sur le tableau de bord |
| `CLASS_CONFIDENCE_THRESHOLD` et métriques lisibles dans les alertes | Alertes affichant `1.000 = 0.000` | Journal d'alertes exploitable (métrique, valeur, seuil, confiance) |
| Relais MQTT des endpoints de simulation en `MODE=HARDWARE` | Console inutilisable avec du matériel réel | Les commandes sont publiées au lieu d'être refusées (plus de `409`) |
| Timeline du scénario exposée par l'API | Frise de jalons vide en `MODE=HARDWARE` | Le frontend affiche la progression quel que soit l'exécutant |
| Consigne publiée **par moteur** (microprogramme, simulateur, banc MQTT) | M2 affichait un `speed_error` de 0,6 % fantôme (consigne de M1 publiée pour les deux) → 8 points de santé perdus en permanence | Les deux rouleaux redeviennent couplés (97,6-98,1 de santé, anomalie ≤ 0,13) |
| Sens de l'hystérésis des règles « en dessous de » (`rule_still_breached`) | « Chute de vitesse 119 tr/min < 90 tr/min » et « Santé 97/100 < 55 » se re-déclenchaient toutes les 60 s à jamais | 35 fausses alertes sur 58 supprimées ; les alertes se referment dès le retour dans la bande morte |
| Horloge et repli FFT rendus identiques côté serveur et client (`useClock`, LCG à graine fixe) | Échec d'hydratation React : page affichée mais inerte (aucun WebSocket, aucun graphique) | Interface interactive dès le premier rendu, console navigateur sans erreur |
| Accumulation des trames dans la file du store (`{ ...get(), ...pending }`) | Deux moteurs dans la même fenêtre de 10 fps s'écrasaient : ~2 points/s au lieu de 8 Hz | Courbes temps réel pleines (≈ 200 points en 12 s au lieu de 25) |
| Disponibilité calculée par une seule fonction (`services/reliability.py`) | 78,8 % dans la salle de contrôle contre 98,95 % dans `/analytics` | Un seul chiffre partagé (temps pondéré par état), écart nul entre les deux pages |
| Callbacks paho-mqtt 2.x (`_on_disconnect` à 5 arguments) | `TypeError` à chaque déconnexion : le pont MQTT ne repartait jamais → `MODE=HARDWARE` inutilisable | Signatures compatibles paho 1.x et 2.x ; la reconnexion automatique fonctionne |
| Broker de test amqtt : greffons déclarés en section `plugins` | Connexion refusée avec le code 128 alors que le broker tournait | `tools/mqtt_broker_test.py` charge explicitement les greffons (`AnonymousAuthPlugin`) |
| Verrou SQLite sous forte charge (`timeout`/`busy_timeout`) | Heartbeat non enregistré → fausse alerte « passerelle hors ligne » | Attente de 30 s sur le verrou + écriture du heartbeat réessayée 3 fois |
| Supervision device sur l'horodatage le plus frais (base **ou** mémoire) | Faux « hors ligne » pendant un pic d'écriture | La carte n'est jamais déclarée hors ligne si sa trame est arrivée |
| Aucune alerte si la passerelle n'a jamais été vue depuis le démarrage | Alerte CRITIQUE « hors ligne » au premier lancement en `MODE=HARDWARE` | État OFFLINE affiché, alerte réservée aux **vraies** coupures (après une première trame) |
