# Déploiement et exploitation — Conveyor AI Ops

Ce document couvre : le démarrage Docker, la bascule vers PostgreSQL/TimescaleDB, l'ajout de
Redis, la mise en production derrière un reverse proxy, la sauvegarde/restauration, la
supervision et la maintenance courante.

---

## 1. Démarrage Docker

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
docker compose logs -f backend
```

Services et dépendances :

| Service | Image | Dépend de | Profil |
|---|---|---|---|
| `mosquitto` | `eclipse-mosquitto:2` | — | défaut |
| `backend` | `./backend` (python:3.11-slim) | mosquitto (sain) | défaut |
| `frontend` | `./frontend` (node:20-alpine) | backend (sain) | défaut |
| `redis` | `redis:7-alpine` | — | `cache` |
| `simulator` | `./backend` (script) | mosquitto (sain) | `edge` |

Volumes persistants : `backend-data` (bases SQLite et artefacts), `mosquitto-data`
(mots de passe, persistance des messages), `mosquitto-log`, `redis-data`.

**Volants de démarrage sain**

* `mosquitto` : `entrypoint.sh` crée les trois comptes à partir des variables
  `MQTT_DEVICE_USER/PASSWORD`, `MQTT_BACKEND_USER/PASSWORD`, `MQTT_MONITOR_USER/PASSWORD`
  (valeurs par défaut dans `.env.example`) puis lance le broker.
* `backend` : `docker-entrypoint.sh` **valide l'artefact IA** (`python -m ml.validate`) et
  **ré-entraîne automatiquement** s'il est absent ou non conforme, attend ensuite le broker
  jusqu'à 20 s, puis démarre uvicorn. La sonde `/api/health` sert de `healthcheck`.
* `frontend` : build Next.js en mode production, `healthcheck` sur la page d'accueil.

Commandes utiles :

```bash
docker compose --profile cache up -d          # + Redis
docker compose --profile edge up -d           # + simulateur de banc MQTT externe
docker compose logs -f --tail=100 backend     # journaux d'ingestion
docker compose restart backend                # redémarrage à chaud
docker compose down                           # arrêt (les données restent)
docker compose down -v                        # arrêt + suppression des volumes
```

---

## 2. PostgreSQL / TimescaleDB

SQLite convient parfaitement à un banc pédagogique (aucune configuration, une seule machine).
Pour plusieurs bancs, des séries longues ou un besoin de haute disponibilité, basculer sur
PostgreSQL (avec l'extension timescaledb pour les hypertables de télémétrie) :

```yaml
# docker-compose.override.yml
services:
  db:
    image: timescale/timescaledb:latest-pg16
    environment:
      POSTGRES_USER: conveyor
      POSTGRES_PASSWORD: conveyor
      POSTGRES_DB: conveyor
    volumes: [db-data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U conveyor"]
      interval: 10s

  backend:
    depends_on:
      db: { condition: service_healthy }
    environment:
      DATABASE_URL: postgresql+psycopg://conveyor:conveyor@db:5432/conveyor

volumes:
  db-data:
```

Ajouter `psycopg[binary]` à `backend/requirements.txt`, puis `docker compose up -d --build`.
La conversion d'une base SQLite existante se fait avec `pgloader` ou un export/import CSV via
`GET /api/export/full.csv`.

---

## 3. Redis (cache et pub/sub)

```bash
docker compose --profile cache up -d
```

Avec `REDIS_ENABLED=true` et `REDIS_URL=redis://redis:6379/0` :

* les métriques de modèle (`/api/models/metrics`) et les agrégats lourds sont mis en cache ;
* les instances backend multiples se synchronisent via pub/sub (les flux WebSocket sont
  diffusés à toutes les instances) ;
* le buffer circulaire peut être partagé, ce qui permet un redémarrage sans trou dans les courbes.

---

## 4. Mise en production

### 4.1 Reverse proxy (HTTPS + WebSocket)

```nginx
server {
  listen 443 ssl http2;
  server_name conveyor.mon-domaine.tn;

  ssl_certificate     /etc/letsencrypt/live/conveyor.mon-domaine.tn/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/conveyor.mon-domaine.tn/privkey.pem;

  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
  }

  location /ws/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;      # les WebSocket temps réel ne doivent pas être coupés
  }
}
```

### 4.2 Liste de contrôle de sécurité

| Point | Action |
|---|---|
| `SECRET_KEY` | Générer une valeur longue et aléatoire (`openssl rand -hex 32`). |
| Mots de passe | Remplacer les comptes de démonstration (`data/seed.json` + `POST /api/auth/users`). |
| CORS | Restreindre `CORS_ORIGINS` au domaine réel (jamais `*`). |
| MQTT | TLS activé, comptes par device, ACL, broker non exposé sur Internet. |
| Base | Utilisateur dédié, mot de passe fort, sauvegardes chiffrées. |
| Journalisation | Conserver `docker compose logs` (rotation) et la table `security_event`. |
| Mises à jour | Reconstruire les images après un `git pull` : `docker compose up -d --build`. |
| Sauvegardes | Volume `backend-data` + export SQL/CSV quotidien (voir § 5). |

---

## 5. Sauvegarde et restauration

```bash
# Sauvegarde de la base SQLite (fonctionne même serveur en marche : mode WAL)
docker compose exec backend sh -c \
  'sqlite3 data/conveyor.db ".backup /tmp/backup.db"' 
docker compose cp backend:/tmp/backup.db ./sauvegardes/conveyor-$(date +%F).db

# Archive complète (base, artefacts, configuration)
docker compose exec backend tar czf /tmp/sauvegarde.tar.gz data artifacts
docker compose cp backend:/tmp/sauvegarde.tar.gz ./sauvegardes/

# Restauration
docker compose stop backend
docker compose cp ./sauvegardes/conveyor-2026-09-17.db backend:/app/data/conveyor.db
docker compose start backend

# PostgreSQL
docker compose exec db pg_dump -U conveyor conveyor | gzip > sauvegardes/pg-$(date +%F).sql.gz
```

Les artefacts IA sont versionnés dans la table `model_version` : après restauration, la page
`/ml` permet de vérifier la version active et de revenir à une autre version si nécessaire.

---

## 6. Supervision

| Cible | Moyen |
|---|---|
| Santé applicative | `GET /api/health` (sonde Docker/K8s) : mode, MQTT, modèle, uptime |
| Santé IA | `GET /api/models/health` : latence d'inférence, mémoire, compteur d'appels |
| Flux temps réel | compteurs `hub` exposés par `/api/overview` (trames/s, octets, clients WebSocket) |
| Équipements | `/devices` : heartbeat, RSSI, perte de paquets, dernière rotation de clé |
| Broker | topics `$SYS/#` (compte `conveyor-monitor`) : clients, messages/s, octets |
| Journaux | `docker compose logs`, en-tête `X-Process-Time-Ms` pour repérer les requêtes lentes |
| Dérive IA | `/api/models/metrics` (dérive entraînement↔production) présenté sur la page `/ml` |

Exemple de sonde de disponibilité :

```bash
curl -fsS localhost:8000/api/health | grep -q '"status":"ok"' || echo "BACKEND DOWN"
```

---

## 7. Maintenance courante

| Périodicité | Opération |
|---|---|
| Quotidien | Vérifier la page `/alerts` (aucune alerte non acquittée), contrôler le heartbeat des devices. |
| Hebdomadaire | Exporter les CSV/PDF d'analyse, vérifier la croissance de la base et la rétention. |
| Mensuel | Ré-entraîner si la dérive augmente (`ml/train.py`), tester le rollback, vérifier les sauvegardes. |
| Trimestriel | Rotation des clés device et MQTT, mise à jour des certificats TLS, revue des comptes. |
| À chaque évolution du banc | Mettre à jour `data/seed.json`, `BASELINE` dans `ml/features.py`, ré-entraîner, relancer `seed_db.py --check`. |

**Rétention** : le planificateur purge la télémétrie brute (`RAW_RETENTION_DAYS = 7`) et les
features anciennes (`FEATURE_RETENTION_DAYS = 90`) en conservant les **agrégats horaires**, qui
alimentent les vues longues (7 j / 30 j) sans croissance illimitée.

**Montée en charge** : à partir de ~10 bancs, prévoir PostgreSQL + Redis, un process uvicorn par
cœur (`--workers N`), et déplacer le broker sur une machine dédiée (Mosquitto tient quelques
milliers de messages/s, mais le réseau d'atelier est souvent le facteur limitant).

---

## 8. Réinitialisation complète (avant une démonstration)

```bash
docker compose down -v                 # tout supprimer (bases, broker, modèles)
docker compose up --build -d           # reconstruire : base fraîche, modèle validé/ré-entraîné
docker compose logs backend | grep Seed
# → Seed terminé : {'motors': 2, 'users': 3, 'rules': 9, 'devices': 1, 'history_points': 678, ...}
```

Alternative sans tout détruire, pour repartir d'un banc propre tout en gardant l'historique :

```bash
python scripts/seed_db.py --apply --reset --history-hours 24
curl -X POST localhost:8000/api/simulator/reset-data
curl -X POST localhost:8000/api/simulator/clear-faults
```
