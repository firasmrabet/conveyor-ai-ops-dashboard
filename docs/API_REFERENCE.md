# Référence API — Conveyor AI Ops

Backend FastAPI : **88 routes** exposées, dont **46 endpoints métier**.
Documentation interactive générée automatiquement :

* Swagger UI : `http://localhost:8000/docs`
* ReDoc : `http://localhost:8000/redoc`
* Schéma OpenAPI : `http://localhost:8000/openapi.json`

Toutes les routes métier sont préfixées par `/api`. Les réponses sont en JSON (UTF-8) ; le
temps de traitement est renvoyé dans l'en-tête `X-Process-Time-Ms`, la compression est activée
au-delà de 1 Ko (`GZip 1024`).

---

## 1. Authentification

| Méthode | Route | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Connexion, renvoie un jeton JWT (`access_token`, `expires_in`, profil). |
| `GET` | `/api/auth/me` | Profil de l'utilisateur courant. |
| `GET` | `/api/auth/users` | Liste des utilisateurs (rôle admin). |
| `POST` | `/api/auth/users` | Création d'un utilisateur (rôle admin). |
| `DELETE` | `/api/auth/users/{user_id}` | Désactivation d'un utilisateur (rôle admin). |

```bash
curl -X POST localhost:8000/api/auth/login -H 'Content-Type: application/json' \
     -d '{"email":"admin@conveyor.local","password":"admin123"}'
```

Comptes créés par l'amorçage : `admin@conveyor.local` / `admin123` (admin),
`operateur@conveyor.local` / `operateur123` (opérateur), `invite@conveyor.local` / `invite123`
(lecture seule). Mots de passe hachés (PBKDF2) — les valeurs ci-dessus sont à changer en
production. Limitations : débit par IP (`RATE_LIMIT_PER_MINUTE`, 600/min par défaut) et
validation Pydantic stricte sur tous les corps de requête.

---

## 2. État du système

| Méthode | Route | Description |
|---|---|---|
| `GET` | `/api/health` | Sonde de santé : mode, uptime, état MQTT, modèle actif et ses métriques, clients WebSocket. |
| `GET` | `/api/overview` | Vue temps réel du Control Room : KPI de flotte, moteurs, dernières alertes, disponibilité. |
| `GET` | `/api/system/info` | Informations de soutenance : versions, chemins d'artefacts, compteurs, planificateur. |
| `GET` | `/` | Fiche d'accueil de l'API (nom, version, routes principales). |

---

## 3. Moteurs

| Méthode | Route | Paramètres principaux |
|---|---|---|
| `GET` | `/api/motors` | — (liste M1/M2 avec santé, anomalie, classe, RUL, vitesse, température, source de donnée) |
| `GET` | `/api/motors/{motor_id}` | `motor_id` ∈ `M1`\|`M2` (fiche complète + baseline) |
| `GET` | `/api/motors/{motor_id}/telemetry` | `from`, `to` (ms epoch), `limit` |
| `GET` | `/api/motors/{motor_id}/features` | `from`, `to`, `limit` |
| `GET` | `/api/motors/{motor_id}/health` | `hours` (fenêtre d'historique de santé) |
| `GET` | `/api/motors/{motor_id}/prediction` | — (dernière inférence complète : classes, RUL, confiance, XAI) |
| `GET` | `/api/motors/{motor_id}/degradation` | `hours` (courbe + projection RUL) |
| `GET` | `/api/motors/{motor_id}/radar` | — (signature actuelle vs baseline saine) |
| `GET` | `/api/motors/{motor_id}/fft` | — (spectre + fréquences caractéristiques BPFO/BPFI/BSF/FTF) |
| `GET` | `/api/motors/{motor_id}/spectrogram` | `minutes` (waterfall temps × fréquence) |
| `GET` | `/api/motors/{motor_id}/anomalies` | `limit` (historique des diagnostics) |
| `GET` | `/api/motors/{motor_id}/xai` | — (contributions + explication rédigée) |
| `GET` | `/api/motors/{motor_id}/interventions` | `limit` |
| `POST` | `/api/motors/{motor_id}/interventions` | `{action, description, technician, duration_min, parts, cost}` |
| `GET` | `/api/motors/{motor_id}/work-orders` | — |
| `GET` | `/api/motors/{motor_id}/report` | `hours` → **PDF téléchargeable** |
| `GET` | `/api/motors/{motor_id}/export.csv` | `from`, `to` → CSV de télémétrie |

```bash
curl -s 'localhost:8000/api/motors/M1/telemetry?from=0&limit=5' | python -m json.tool
curl -o rapport-M1.pdf localhost:8000/api/motors/M1/report
curl -o telemetrie-M1.csv 'localhost:8000/api/motors/M1/export.csv?from=0'
```

Réponse type de `GET /api/motors` (extrait réel) :

```json
[{
  "id": "M1", "name": "Moteur M1", "label": "Moteur GAUCHE", "side": "LEFT",
  "state": "FAULT", "health_index": 54.69, "anomaly_score": 0.5731,
  "fault_class": "UNBALANCE", "rul_hours": 209.0,
  "rpm": 119.87, "power_w": 12.4, "temp_bearing_c": 48.1, "vibration_rms": 0.4486,
  "edge_health_index": 71.5, "model_version": "v1.4.0", "baseline": {"vibration_rms": 0.082, "...": "..."}
}]
```

---

## 4. Analyse

| Méthode | Route | Description |
|---|---|---|
| `GET` | `/api/analytics/summary` | `range` ∈ `1h\|6h\|24h\|7d\|30d` — synthèse de la fenêtre |
| `GET` | `/api/analytics/heatmap` | Heatmap santé (jour de semaine × heure) |
| `GET` | `/api/analytics/comparison` | Comparateur M1 vs M2 + écart de synchronisation |
| `GET` | `/api/analytics/correlation` | Corrélations température ↔ courant ↔ vibration |
| `GET` | `/api/analytics/fault-distribution` | Donut + Pareto des défauts |
| `GET` | `/api/analytics/reliability` | MTBF, MTTR, arrêts, faux positifs |
| `GET` | `/api/analytics/hourly` | Agrégats horaires (tendances longues) |
| `GET` | `/api/analytics/export.csv` | Export CSV consolidé |
| `GET` | `/api/analytics/report` | Rapport PDF global (M1 + M2) |

---

## 5. Alertes, règles et maintenance

| Méthode | Route | Description |
|---|---|---|
| `GET` | `/api/alerts` | Liste filtrée (`severity`, `status`, `motor_id`, `search`, `limit`, `offset`) |
| `GET` | `/api/alerts/live` | Dernières alertes du buffer mémoire (temps réel) |
| `PATCH` | `/api/alerts/{id}` | Acquitter / assigner / commenter (`{status, assigned_to, comment}`) |
| `POST` | `/api/alerts/{id}/work-order` | Créer un ordre de travail depuis une alerte (201) |
| `DELETE` | `/api/alerts/{id}` | Supprimer une alerte |
| `GET` | `/api/rules` | Liste des 9 règles de seuil |
| `POST` | `/api/rules` | Créer une règle (201) |
| `PUT` | `/api/rules/{id}` | Modifier une règle |
| `PATCH` | `/api/rules/{id}/toggle` | Activer / désactiver |
| `DELETE` | `/api/rules/{id}` | Supprimer |
| `GET` | `/api/notifications/log` | Journal des notifications envoyées |
| `POST` | `/api/notifications/test/{channel}` | Test d'envoi (`inapp`, `son`, `email`, `webhook`) |
| `GET` | `/api/maintenance/prescriptions` | Recommandations prescriptives générées par l'IA |
| `GET` | `/api/maintenance/work-orders` | Tous les ordres de travail |
| `POST` | `/api/maintenance/work-orders` | Créer un ordre de travail (201) |
| `PATCH` | `/api/maintenance/work-orders/{id}` | Mettre à jour (statut, assignation, priorité) |
| `GET` | `/api/maintenance/interventions` | Journal des interventions |
| `POST` | `/api/maintenance/interventions` | Enregistrer une intervention (201) |
| `GET` | `/api/maintenance/stats` | MTBF / MTTR / coûts / taux de faux positifs |

Chaque règle porte : `metric`, `operator`, `threshold`, `hysteresis`, `duration_s`,
`debounce_s`, `severity`, `channels`, `enabled`. L'alerte générée conserve `metric`, `value`,
`threshold`, `origin` (`RULES`/`ML`) et un `context` exploitable (probabilités, explication,
recommandation, RUL, santé).

---

## 6. Intelligence artificielle (MLOps)

| Méthode | Route | Description |
|---|---|---|
| `GET` | `/api/models` | Modèles disponibles + modèle actif |
| `GET` | `/api/models/metrics` | Confusion, ROC/PR, dérive entraînement↔production, importances (cache 30 s) |
| `GET` | `/api/models/rollback-candidates` | Versions disponibles pour un rollback |
| `POST` | `/api/models/rollback` | Recharger un artefact antérieur (hot-swap) |
| `POST` | `/api/models/reload` | Recharger l'artefact actif sans redémarrer le service |
| `POST` | `/api/models/sync` | Synchroniser les métriques de l'artefact vers la base |
| `GET` | `/api/models/edge-comparison` | Comparaison TinyML (edge) vs modèles serveur |
| `GET` | `/api/models/health` | Latence d'inférence, mémoire, version, compteur d'appels |

```bash
curl -X POST localhost:8000/api/models/rollback -H 'Content-Type: application/json' \
     -d '{"name":"conveyor-synth","version":"v1.2.0"}'
curl -s localhost:8000/api/models/edge-comparison | python -m json.tool
```

---

## 7. Équipements

| Méthode | Route | Description |
|---|---|---|
| `GET` | `/api/devices` | Liste des passerelles avec état (heartbeat, RSSI, cadence) |
| `GET` | `/api/devices/network` | Diagnostic réseau (latence, débit, pertes) |
| `GET` | `/api/devices/security` | TLS, authentification, rotation de clés, événements de sabotage |
| `POST` | `/api/devices/{device_id}/command` | Actions : `reboot`, `recalibrate`, `ota_update`, `rotate_key`, `set_sampling`, `test_network` |
| `GET` | `/api/devices/{device_id}/logs` | Journal d'événements du device |
| `POST` | `/api/devices/{device_id}/simulate-network-event` | Injecter une perturbation réseau (démonstration) |
| `POST` | `/api/devices/{device_id}/reset-tamper` | Réarmer le drapeau de sabotage |

```bash
curl -X POST localhost:8000/api/devices/esp32-conveyor-01/command \
  -H 'Content-Type: application/json' -d '{"action":"set_sampling","payload":{"hz":20}}'
```

Chaque commande est **publiée sur MQTT** (`conveyor/cmd/{device}/{action}`) et renvoie la clé
`forwarded` (`topic`, `payload`, `published`).

---

## 8. Simulateur (console de démonstration)

| Méthode | Route | Description |
|---|---|---|
| `GET` | `/api/simulator/state` | État complet : physique des moteurs, défauts actifs, compteurs MQTT, disponibilité |
| `GET` | `/api/simulator/faults` | Catalogue des 8 défauts injectables (libellé, icône, description) |
| `POST` | `/api/simulator/control` | `{running, speed_rpm, load_kg, parcels_per_min, ramp_s}` |
| `POST` | `/api/simulator/inject-fault` | `{motor_id, fault, intensity, degradation_rate, auto_clear_after_s}` |
| `POST` | `/api/simulator/clear-faults` | Retour à l'état sain |
| `POST` | `/api/simulator/scenario` | `{name: "demo-3min"}` — lance le scénario de soutenance |
| `GET` | `/api/simulator/scenario` | État courant **+ timeline des 12 jalons** + `executor` (`backend` \| `device`) |
| `POST` | `/api/simulator/stop-scenario` | Interrompt le scénario |
| `POST` | `/api/simulator/fault-sequence` | Rotation automatique des défauts (`motor_id`, `step_s`) |
| `POST` | `/api/simulator/reset-data` | Vide télémétrie, inférences, alertes, ordres de travail (`keep_rules`) |

En `MODE=HARDWARE`, ces endpoints renvoient `{"mode":"HARDWARE","available":false,"forwarded":{…}}`
après avoir publié la commande correspondante sur MQTT.

---

## 9. Rapports et exports

| Méthode | Route | Description |
|---|---|---|
| `POST` | `/api/reports/generate` | Génère un PDF (`motor_id`, `period_hours`, `format`) et le renvoie en flux |
| `GET` | `/api/motors/{id}/report` | Raccourci GET pour le rapport d'un moteur |
| `GET` | `/api/analytics/report` | Rapport d'analyse global |
| `GET` | `/api/motors/{id}/export.csv` | CSV de télémétrie d'un moteur |
| `GET` | `/api/analytics/export.csv` | CSV d'analyse consolidé |
| `GET` | `/api/export/full.csv` | CSV complet (télémétrie + inférences) |

Contenu du PDF moteur : identité de l'actif, KPI de la période, indice de santé et delta,
diagnostic IA (classe, confiance, RUL avec intervalle), explications XAI, spectre et marqueurs
de défaut, historique des alertes, ordres de travail et interventions, plan d'action prescriptif,
en-tête (`REPORT_COMPANY` / `REPORT_AUTHOR`) et empreinte de génération.

---

## 10. Temps réel (WebSocket)

| Route | Description |
|---|---|
| `WS /ws/telemetry` | Flux unifié : `telemetry`, `features`, `inference`, `edge_inference`, `alert`, `status`, `scenario`, `device_command`, `heartbeat` |

Format des messages : `{"type": "...", "motor_id": "M1", "data": {…}}`.
Un client peut s'abonner à un sous-ensemble (`?motors=M1`). Le serveur diffuse à ~8-10 Hz par
moteur ; le frontend échantillonne à ~10 fps et conserve 300-600 points par série.

```javascript
const socket = new WebSocket(`ws://localhost:8000/ws/telemetry`);
socket.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.type === "telemetry") updateGauge(message.data);
};
```

---

## 11. Codes d'erreur

| Code | Signification | Exemple |
|---|---|---|
| `400` | Payload invalide (Pydantic) | Seuil manquant dans la création d'une règle |
| `401` | Jeton absent ou expiré | Accès à `/api/auth/me` sans `Authorization` |
| `403` | Rôle insuffisant | Un opérateur tente de créer un utilisateur |
| `404` | Ressource inconnue | `GET /api/motors/M3` |
| `409` | Conflit d'état | Commande device sur une passerelle hors ligne |
| `422` | Validation de type/quorum | `intensity` hors bornes |
| `429` | Limitation de débit | Plus de 600 requêtes/min depuis la même IP |
| `500` | Erreur interne | Trace complète dans les journaux du conteneur backend |

En cas d'erreur, le corps suit le format FastAPI : `{"detail": "message explicite"}`.
