"""
Calculs de fiabilité de la ligne — source de vérité unique
==========================================================

La disponibilité est la grandeur la plus regardée en soutenance : elle doit être la
MÊME dans la salle de contrôle (`GET /api/overview`) et dans la page analytique
(`GET /api/analytics/reliability`). Deux formules différentes y donnaient auparavant
deux valeurs incompatibles (78,8 % contre 98,95 % pour la même fenêtre).

Définition retenue, conforme à la pratique de la maintenance industrielle :

    disponibilité = temps passé dans un état exploitable / temps total observé

- états exploitables : RUNNING et DEGRADED (la ligne produit encore, même dégradée) ;
- états non exploitables : STOPPED, FAULT, MAINTENANCE, OFFLINE.

Le temps est reconstitué à partir des snapshots de santé (un échantillon toutes les
5 s) en pondérant chaque échantillon par l'intervalle qui le sépare du suivant : un
état qui dure compte donc plus qu'un état fugace, contrairement à un simple
comptage d'échantillons. Les segments plus longs que `MAX_SEGMENT_MS` sont
plafonnés pour qu'une coupure de collecte (backend arrêté) ne soit pas comptée
comme du temps de marche.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlmodel import Session, select

from ..models import HealthSnapshot, now_ts_ms

#: États pendant lesquels la ligne est considérée comme disponible.
AVAILABLE_STATES: Tuple[str, ...] = ("RUNNING", "DEGRADED")

#: États pendant lesquels la ligne est indisponible (utilisé pour la lisibilité).
UNAVAILABLE_STATES: Tuple[str, ...] = ("STOPPED", "FAULT", "MAINTENANCE", "OFFLINE")

#: Plafond d'un intervalle de mesure (s) : au-delà, la collecte était interrompue.
MAX_SEGMENT_MS: int = 300_000


def _segments(session: Session, motor_id: str, since_ms: int,
              now_ms: Optional[int] = None) -> List[Tuple[str, int]]:
    """Retourne [(état, durée pondérée ms)] pour un moteur depuis `since_ms`.

    Chaque échantillon porte la durée qui le sépare du suivant (état constant entre
    deux mesures). Le dernier échantillon est prolongé jusqu'à `now_ms` — plafonné —
    puisque cet état est celui qui est encore en cours à l'instant du calcul.
    """
    rows = session.exec(
        select(HealthSnapshot.ts, HealthSnapshot.state)
        .where(HealthSnapshot.motor_id == motor_id, HealthSnapshot.ts >= since_ms)
        .order_by(HealthSnapshot.ts)
    ).all()
    if not rows:
        return []
    # SQLModel peut renvoyer des tuples ou des lignes scalaires selon le driver.
    samples: List[Tuple[int, str]] = []
    for row in rows:
        if isinstance(row, (tuple, list)) and len(row) == 2:
            samples.append((int(row[0]), str(row[1] or "RUNNING")))
        else:  # pragma: no cover - dépendance au dialecte
            samples.append((int(getattr(row, "ts", 0)), str(getattr(row, "state", "RUNNING"))))
    if len(samples) == 1:
        return [(samples[0][1], MAX_SEGMENT_MS)]
    segments: List[Tuple[str, int]] = []
    for index, (ts, state) in enumerate(samples[:-1]):
        gap = min(samples[index + 1][0] - ts, MAX_SEGMENT_MS)
        segments.append((state, max(0, gap)))
    # Dernier échantillon : il représente l'état en cours, donc il court jusqu'à
    # maintenant (plafonné). S'il vient d'être écrit, on lui prête la durée moyenne
    # observée pour ne pas sous-pondérer un état très récent.
    end = int(now_ms if now_ms is not None else now_ts_ms())
    trailing = min(max(end - samples[-1][0], 0), MAX_SEGMENT_MS)
    if trailing <= 0:
        average = sum(duration for _, duration in segments) // max(len(segments), 1) if segments else 1000
        trailing = max(1000, min(average, MAX_SEGMENT_MS))
    segments.append((samples[-1][1], trailing))
    return segments


def availability_pct(session: Session, motor_ids: Sequence[str] = ("M1", "M2"),
                     since_ms: Optional[int] = None, window_h: float = 24.0,
                     now_ms: Optional[int] = None) -> Optional[float]:
    """Disponibilité (%) pondérée dans le temps pour un ou plusieurs moteurs.

    Args:
        session: session SQLModel.
        motor_ids: moteurs pris en compte (par défaut les deux rouleaux).
        since_ms: début de la fenêtre (ms epoch) ; prioritaire sur `window_h`.
        window_h: profondeur de la fenêtre en heures quand `since_ms` est absent.
        now_ms: fin de l'observation (ms epoch) — permet de rejouer un historique en test.

    Returns:
        Le pourcentage arrondi à 0,01, ou `None` si aucun snapshot n'est disponible
        (base vide) : l'appelant choisit alors sa valeur de repli.
    """
    start = int(since_ms if since_ms is not None else now_ts_ms() - window_h * 3_600_000)
    total_ms = 0
    available_ms = 0
    for motor_id in motor_ids:
        for state, duration in _segments(session, motor_id, start, now_ms=now_ms):
            total_ms += duration
            if state in AVAILABLE_STATES:
                available_ms += duration
    if total_ms <= 0:
        return None
    return round(available_ms / total_ms * 100.0, 2)


def stops_count(session: Session, motor_id: str, since_ms: int) -> int:
    """Nombre d'arrêts : transitions d'un état exploitable vers un état non exploitable."""
    rows = session.exec(
        select(HealthSnapshot.state)
        .where(HealthSnapshot.motor_id == motor_id, HealthSnapshot.ts >= since_ms)
        .order_by(HealthSnapshot.ts)
    ).all()
    states = [str(row[0] if isinstance(row, (tuple, list)) else row) for row in rows]
    stops = 0
    for previous, current in zip(states, states[1:]):
        if previous in AVAILABLE_STATES and current in UNAVAILABLE_STATES:
            stops += 1
    return stops


def downtime_hours(availability: Optional[float], window_h: float) -> Optional[float]:
    """Temps d'indisponibilité (h) déduit de la disponibilité, pour l'affichage."""
    if availability is None:
        return None
    return round(window_h * max(0.0, 100.0 - availability) / 100.0, 2)


def reliability_bundle(session: Session, motor_ids: Iterable[str] = ("M1", "M2"),
                       since_ms: Optional[int] = None, window_h: float = 24.0) -> Dict[str, Any]:
    """Bloc de fiabilité prêt à publier : disponibilité par moteur + disponibilité ligne."""
    per_motor: Dict[str, Optional[float]] = {}
    for motor_id in motor_ids:
        per_motor[motor_id] = availability_pct(session, [motor_id], since_ms=since_ms, window_h=window_h)
    known = [value for value in per_motor.values() if value is not None]
    line = round(sum(known) / len(known), 2) if known else None
    return {"window_h": window_h, "per_motor": per_motor, "line": line}
