"""Couche base de données : engine SQLModel, création des tables, session."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

# --- Préparation du dossier SQLite éventuel -------------------------------
if settings.database_url.startswith("sqlite"):
    db_path = settings.database_url.split("///")[-1]
    if db_path and not db_path.startswith(":memory:"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

# `timeout` : sous charge réelle (télémétrie 8 Hz × plusieurs moteurs, tâches du
# planificateur, requêtes de l'interface), deux écritures peuvent se disputer le
# verrou d'écriture SQLite. Sans délai d'attente, la seconde échoue immédiatement
# (« database is locked ») : c'était la cause des heartbeats non enregistrés, donc
# des fausses alertes « passerelle hors ligne » alors que la carte publiait.
connect_args = ({"check_same_thread": False, "timeout": 30}
                if settings.database_url.startswith("sqlite") else {})

engine = create_engine(
    settings.database_url,
    echo=settings.db_echo,
    connect_args=connect_args,
    pool_pre_ping=True,
)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - dépend du backend
    """Réglages SQLite : WAL + clés étrangères (concurrence ingestion/API)."""
    if settings.database_url.startswith("sqlite"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        # Attente (ms) avant d'abandonner sur un verrou d'écriture concurrent.
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


def init_db() -> None:
    """Crée toutes les tables (idempotent)."""
    from . import models  # noqa: F401  (enregistre les tables)

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """Dépendance FastAPI."""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session autonome pour les tâches de fond (simulateur, scheduler)."""
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
