"""Utilitaires partagés par les services (logging, helpers temps, HA)."""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from ..config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("conveyor")

# APScheduler journalise chaque exécution en INFO : trop verbeux pour un dashboard
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)


def now_ms() -> int:
    return int(time.time() * 1000)


def iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def moving_average(values: Iterable[float], window: int = 5) -> List[float]:
    out: List[float] = []
    buf: List[float] = []
    for v in values:
        buf.append(float(v))
        if len(buf) > window:
            buf.pop(0)
        out.append(sum(buf) / len(buf))
    return out


def ewma(previous: float, value: float, alpha: float = 0.2) -> float:
    return previous + alpha * (value - previous)


def sign_payload(payload: Dict[str, Any]) -> str:
    """Signature du journal d'interventions (intégrité, chaînage horodaté)."""
    raw = "|".join(f"{k}={payload[k]}" for k in sorted(payload.keys()))
    return hashlib.sha256(f"{raw}|{settings.secret_key}".encode()).hexdigest()[:32]


def uptime_human(seconds: float) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}j")
    if h or d:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    if not d:
        parts.append(f"{s}s")
    return " ".join(parts)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def downsample_factor(dt_s: float, target_hz: float = 1.0) -> int:
    """Facteur de sous-échantillonnage pour la persistance de la télémétrie brute."""
    if dt_s <= 0:
        return 1
    return max(1, int(round(dt_s * target_hz)))


def compare_metric(value: float, operator: str, threshold: float) -> bool:
    return {
        ">": value > threshold,
        ">=": value >= threshold,
        "<": value < threshold,
        "<=": value <= threshold,
    }.get(operator, False)


def rule_still_breached(value: float, operator: str, threshold: float, hysteresis: float) -> bool:
    """Le seuil reste-t-il franchi en tenant compte de la bande morte (hystérésis) ?

    L'hystérésis empêche le clignotement d'une alerte quand la mesure oscille autour
    du seuil. La bande morte s'étend TOUJOURS du côté opposé à la règle :

    - règle « au-dessus de » (`>` / `>=`) : l'alerte reste active tant que la mesure
      n'est pas redescendue sous `seuil - hystérésis` (ex. anomalie 0,65 → 0,60) ;
    - règle « en dessous de » (`<` / `<=`) : l'alerte reste active tant que la mesure
      n'est pas remontée au-dessus de `seuil + hystérésis` (ex. vitesse 90 → 95 tr/min).

    En inversant ce signe, une alerte de type « Chute de vitesse » restait active à
    jamais dès qu'elle s'était déclenchée une fois : elle se re-déclenchait à chaque
    période de temporisation en affichant la vitesse normale (119 tr/min < 90 tr/min).
    """
    if operator in (">", ">="):
        return compare_metric(value, operator, threshold - hysteresis)
    if operator in ("<", "<="):
        return compare_metric(value, operator, threshold + hysteresis)
    return False
