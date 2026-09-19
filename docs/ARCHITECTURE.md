# Architecture — Conveyor AI Ops

Ce document décrit les composants du système, les flux de données et les choix techniques
qui les justifient. Il complète le schéma ASCII du `README.md` par le détail de chaque couche.

---

## 1. Vue logique

| Couche | Composant | Technologie | Responsabilité |
|---|---|---|---|
| **Terrain** | Passerelle ESP32 | Arduino / FreeRTOS | Acquisition, calcul embarqué, publication MQTT, pré-diagnostic, application des commandes |
| **Transport** | Broker | Eclipse Mosquitto | Découplage producteur/consommateur, niveaux de service, ACL, persistance, TLS |
| **Application** | Backend | FastAPI (Python 3.11) | Validation, persistance, IA lourde, alerting, maintenance, rapports, API/WS |
| **Présentation** | Frontend | Next.js 14 + TypeScript | Visualisation temps réel, jumeau numérique, analyse, pilotage, configuration |
| **Exploitation** | Docker Compose | — | Orchestration, isolation réseau, volants de démarrage sain |

## 2. Flux de données

```
   ①  ESP32 : 500 Hz acquisition → buffers circulaires → features 1 Hz + pré-diagnostic
   ②  MQTT  : telemetry 8 Hz · features 1 Hz · edge_inference 1 Hz · status 5 Hz · heartbeat 1 Hz
   ③  Ingestion backend :
         valider (Pydantic) → buffer mémoire 10 s → diffuser (WebSocket)
                                                 → persister (sous-échantillonnage ~1 Hz)
   ④  IA      : features (edge si présentes, sinon extraites côté serveur)
                 → anomalie + classe + RUL + contributions XAI → indice de santé
   ⑤  Alerting: seuils (hystérésis + durée + anti-spam) et IA (3 confirmations + temporisation)
                 → alertes + notifications (in-app, son, e-mail, webhook) + ordre de travail
   ⑥  UI      : WebSocket (temps réel) + REST (historique) → pages, jumeau 3D, courbes
   ⑦  Descente: commandes UI → POST /api/simulator/* ou /api/devices/{id}/command
                 → conveyor/cmd/{M1|M2|all}/{command} → carte
```

Le **sens montant** et le **sens descendant** passent par les mêmes briques, ce qui permet de
tester l'ensemble de la chaîne sans matériel (le mode `HARDWARE` remplaçant le générateur
interne par la carte réelle sans changer une ligne de code applicatif).

## 3. Backend : découpage

| Module | Fichier | Rôle |
|---|---|---|
| Configuration | `app/config.py` | Paramètres (pydantic-settings), origines CORS, seuils d'anti-flapping, mode. |
| Base | `app/db.py` | Moteur SQLModel, sessions, WAL SQLite, création des tables. |
| Modèles | `app/models.py` | 15 tables : moteurs, télémétrie, features, inférences, snapshots de santé, agrégats horaires, alertes, règles, équipements, événements de sécurité, ordres de travail, interventions, utilisateurs, versions de modèles, paramètres. |
| Schémas | `app/schemas.py` | Contrats Pydantic : payloads MQTT (télémétrie, features, inférence edge, statut, heartbeat), inférence serveur, commandes, requêtes d'API. |
| Sécurité | `app/security.py` | Hachage des mots de passe, jetons JWT, rôles, dépendances d'authentification, rotation de clés. |
| Temps réel | `app/realtime.py` | Buffers circulaires par moteur, concentrateur WebSocket, compteur de trames, Redis optionnel. |
| Ingestion | `app/services/ingestion.py` | Point d'entrée unique (MQTT **et** simulateur) : validation → buffers → persistance → IA → alertes. |
| Service IA | `app/services/ml_service.py` | Chargement/rollback d'artefact, inférence, lissage EMA, verrouillage de classe, spectrogramme, comparaison edge/serveur. |
| Alerting | `app/services/alerting.py` | Moteur de seuils et de diagnostic, anti-flapping, notifications, prescriptions, ordres de travail. |
| Simulateur | `app/services/simulator.py` | Boucle asyncio de la physique, console de contrôle, scénario de démonstration. |
| MQTT | `app/services/mqtt_client.py` | Pont paho-mqtt (thread), abonnements, publication de commandes, statistiques. |
| Planificateur | `app/services/scheduler.py` | APScheduler : agrégats horaires, purge de rétention, supervision des heartbeat, snapshot de santé. |
| Rapports | `app/services/reporting.py` | Construction des PDF (ReportLab) : moteur, multijournalier, analyse. |
| Fiabilité | `app/services/reliability.py` | Source unique du calcul de disponibilité (temps pondéré par état) : partagé par `/api/overview` et `/api/analytics/reliability` pour que la salle de contrôle et l'analyse historique affichent le même chiffre. |
| Amorçage | `app/services/seed.py` | Base de démonstration : 24 h d'historique, comptes, règles, modèles, buffers préremplis. |

### Choix techniques justifiés

* **Un seul point d'entrée d'ingestion** (`ingest_telemetry`) : le simulateur et le broker MQTT
  empruntent exactement le même chemin, ce qui élimine la classe de bugs « ça marche en
  simulation mais pas avec le matériel ».
* **Buffer mémoire de 10 s** en plus de la base : les courbes à 8-10 Hz restent fluides sans
  solliciter le disque ; la persistance est sous-échantillonnée (~1 Hz) pour tenir la rétention.
* **Sous-échantillonnage à l'ingestion** plutôt qu'à la lecture : coût CPU constant quel que
  soit le nombre de clients connectés.
* **Anti-flapping explicite** (hystérésis + durée + temporisation + confirmations IA) : c'est la
  condition pour que les alertes soient exploitables en atelier ; le seuil est visible dans
  l'interface et modifiable à chaud.
* **Fusion pondérée de l'indice de santé** plutôt qu'un score unique de modèle : la maintenance
  raisonne en « santé / gravité / temps restant », pas en probabilité brute.
* **SQLModel** (SQLAlchemy + Pydantic) : un seul modèle de données pour la base et l'API, et
  bascule SQLite → PostgreSQL/TimescaleDB par simple `DATABASE_URL`.

## 4. Frontend : découpage

| Dossier | Contenu |
|---|---|
| `src/app/` | Les 8 pages (App Router) : `page.tsx` (Control Room), `motors/[id]`, `analytics`, `alerts`, `ml`, `devices`, `simulator`, `settings`. |
| `src/components/ui/` | Primitives du design system (cartes vitrées, badges, boutons, jauges, squelettes). |
| `src/components/charts/` | `LiveChart` (canvas, 60 s glissantes), `RealtimeChart`, `FftSpectrum`, jauges radiales. |
| `src/components/three/` | `ConveyorScene` : jumeau numérique react-three-fiber (bande, poulies, moteurs, points chauds). |
| `src/components/motors/` | `MotorCard`, `AlertFeed` : cartes moteur et fil d'alertes. |
| `src/components/simulator/` | `FaultGrid`, `ControlSliders`, `ScenarioTimeline`. |
| `src/lib/` | `api.ts` (client REST), `socket.ts` (WebSocket avec reconnexion et repli), `store.ts` (Zustand), `hooks.ts` (useLive, useMotors…), `types.ts`, `format.ts`, `providers.tsx` (thème + TanStack Query). |

* **Throttling côté client** : le flux WebSocket est échantillonné à ~10 fps maximum dans les
  histogrammes, quel que soit le débit serveur (8-10 Hz × 2 moteurs + features + inférences).
* **Buffers circulaires côté client** (300 à 600 points selon la série) : mémoire bornée,
  rendu canvas pour les courbes haute fréquence.
* **Reconnexion avec repli exponentiel** : `socket.ts` reprend automatiquement après une
  coupure réseau et indique l'état dans l'en-tête (pastille « MQTT LIVE » clignotante).
* **Appels API relatifs** (`/api/...`) + proxy Next : aucun problème de CORS, aucune URL de
  service codée en dur dans le navigateur (compatible avec un déploiement derrière un reverse proxy).

## 5. Chemin d'une donnée, de bout en bout

1. Le MPU6050 fournit un échantillon d'accélération toutes les 2 ms ; la carte empile 10
   échantillons par rafale et remplit un buffer circulaire de 2 048 valeurs.
2. Toutes les secondes, la carte calcule RMS, crête, kurtosis, facteur de crête, pics FFT
   (1×, 2×, 3×, bande roulement), température, courant, vitesse et en déduit un pré-diagnostic.
3. Les trames partent sur MQTT ; le backend les valide, les bufferise et les rediffuse en
   WebSocket. La télémétrie est écrite en base à ~1 Hz.
4. Le moteur IA produit l'indice de santé, la classe de défaut, le RUL et les contributions ;
   l'anti-flapping décide de publier ou non une alerte ; la prescription crée éventuellement
   un ordre de travail.
5. Le frontend affiche : le jumeau 3D change de couleur, la jauge descend, la courbe se
   colore, l'alerte apparaît dans le fil et la page `/alerts` propose l'action prescriptive.
6. L'opérateur peut acquitter, assigner, commenter puis générer le rapport PDF ; le journal
   d'interventions est signé et exploité par les statistiques MTBF/MTTR.

## 6. Performances et dimensionnement

| Grandeur | Valeur mesurée (banc simulé, 2 moteurs à 8 Hz) |
|---|---|
| Charge CPU backend | < 15 % d'un cœur |
| Latence bout en bout (capteur → écran) | 250-400 ms |
| Débit MQTT | ~20 messages/s, ~28 Ko/s |
| Écritures base | ~2 lignes/s (+ 2 snapshots / 5 s) |
| Endpoint `/api/motors` | ~50 ms (p95 < 170 ms) sur 24 h d'historique |
| Mémoire backend | ~180 Mo (dont scikit-learn) |
| Taille de l'artefact IA | 1,7 Mo |

Pour passer à l'échelle (plusieurs dizaines de bancs) : activer Redis (`--profile cache`),
basculer `DATABASE_URL` sur PostgreSQL/TimescaleDB, déployer plusieurs instances backend
derrière un reverse proxy (le pub/sub Redis synchronise les flux WebSocket).

## 7. Extensions prévues (non bloquantes)

* **MQTT Sparkplug B** : enveloppe normalisée pour l'intégration SCADA.
* **OPC-UA** : passerelle vers un automate industriel.
* **Kafka/InfluxDB** : variante de la couche transport pour des cadences supérieures à 100 Hz.
* **Conteneurisation du firmware en CI** : `pio run` dans une étape de build pour garantir la
  compilation à chaque modification du sketch.
