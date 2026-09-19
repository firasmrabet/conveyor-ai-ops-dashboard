#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests de non-régression — règles d'alerte et calculs partagés
============================================================

Ces tests couvrent la logique pure (sans base de données ni broker MQTT) qui a
déjà provoqué des défauts en exploitation :

1. **sens de l'hystérésis** — une alerte « en dessous de » restait active
   indéfiniment après un premier déclenchement (vitesse 119 tr/min annoncée comme
   « 119 < 90 », santé 97/100 annoncée comme « 97 < 55 »), d'où 35 fausses alertes
   sur 58 en base ;
2. **comparaison de seuils** — opérateurs stricts et non stricts ;
3. **pondération de la disponibilité** — un état long doit peser plus qu'un état
   fugace, et une coupure de collecte ne doit pas être comptée comme du temps de
   marche.

Exécution :
    python backend/tests/test_alert_rules.py        # sans dépendance externe
    pytest backend/tests/ -q                        # si pytest est installé
"""
from __future__ import annotations

import sys
from pathlib import Path

# --- Accès au paquet `app` (le dossier backend doit être dans sys.path) ------
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.common import compare_metric, rule_still_breached  # noqa: E402


# =============================================================================
#  1. Comparaison de seuils
# =============================================================================
def test_compare_metric_operators() -> None:
    """Les quatre opérateurs du catalogue de règles."""
    assert compare_metric(0.70, ">", 0.65) is True
    assert compare_metric(0.65, ">", 0.65) is False          # strict
    assert compare_metric(0.65, ">=", 0.65) is True          # non strict
    assert compare_metric(78.0, "<", 90.0) is True
    assert compare_metric(90.0, "<", 90.0) is False          # strict
    assert compare_metric(90.0, "<=", 90.0) is True
    assert compare_metric(90.0, "??", 90.0) is False         # opérateur inconnu


# =============================================================================
#  2. Hystérésis : maintien et refermeture d'une alerte
# =============================================================================
def test_hysteresis_keeps_alert_inside_dead_band_above() -> None:
    """Règle « au-dessus de » : la bande morte s'étend SOUS le seuil."""
    # Avertissement vibration 0,35 g avec 0,05 g d'hystérésis
    assert rule_still_breached(0.36, ">", 0.35, 0.05) is True    # encore au-dessus
    assert rule_still_breached(0.33, ">", 0.35, 0.05) is True    # dans la bande morte
    assert rule_still_breached(0.29, ">", 0.35, 0.05) is False   # sortie : alerte refermée
    # Anomalie IA 0,65 avec 0,05
    assert rule_still_breached(0.62, ">", 0.65, 0.05) is True
    assert rule_still_breached(0.19, ">", 0.65, 0.05) is False


def test_hysteresis_closes_below_rules() -> None:
    """Règle « en dessous de » : la bande morte s'étend AU-DESSUS du seuil.

    C'est le défaut historique : le signe était inversé, donc la mesure repassée
    au-dessus du seuil concluait « toujours en défaut » et re-déclenchait l'alerte
    à chaque période de temporisation.
    """
    # Chute de vitesse : 90 tr/min avec 5 tr/min d'hystérésis
    assert rule_still_breached(77.6, "<", 90.0, 5.0) is True     # toujours en défaut
    assert rule_still_breached(92.0, "<", 90.0, 5.0) is True     # dans la bande morte
    assert rule_still_breached(119.4, "<", 90.0, 5.0) is False   # ligne revenue à sa cadence
    # Santé moteur basse : 55/100 avec 10 points d'hystérésis
    assert rule_still_breached(51.6, "<", 55.0, 10.0) is True
    assert rule_still_breached(62.0, "<", 55.0, 10.0) is True
    assert rule_still_breached(97.9, "<", 55.0, 10.0) is False
    # Température roulement 65 °C avec 3 °C
    assert rule_still_breached(66.5, "<=", 65.0, 3.0) is True
    assert rule_still_breached(69.5, "<=", 65.0, 3.0) is False


def test_hysteresis_is_symmetric_in_intent() -> None:
    """Les deux familles de règles se referment symétriquement autour du seuil.

    Seuil 65, hystérésis 3 : la règle « au-dessus » se referme à 62 (65 − 3), la règle
    « en dessous » à 68 (65 + 3) — même largeur de bande morte de part et d'autre.
    """
    assert rule_still_breached(62.5, ">", 65.0, 3.0) is True    # dans la bande morte
    assert rule_still_breached(61.9, ">", 65.0, 3.0) is False   # alerte refermée
    assert rule_still_breached(67.5, "<", 65.0, 3.0) is True    # dans la bande morte
    assert rule_still_breached(68.1, "<", 65.0, 3.0) is False   # alerte refermée


# =============================================================================
#  3. Disponibilité pondérée dans le temps
# =============================================================================
def test_availability_weights_states_by_duration() -> None:
    """Un état long pèse plus qu'un état fugace (contrairement au comptage brut)."""
    from app.services import reliability

    class FakeSession:
        """Session SQLModel minimale : rejoue une liste (ts, state) prédéfinie."""

        def __init__(self, samples):
            self._samples = samples

        def exec(self, _statement):  # noqa: D401 - signature imitée
            return _Result(list(self._samples))

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    # 100 s en marche puis 10 s en défaut → 100/110 = 90,91 %.
    value = reliability.availability_pct(
        FakeSession([(0, "RUNNING"), (100_000, "FAULT")]), ["M1"], since_ms=0,
        now_ms=110_000)  # type: ignore[arg-type]
    assert value == 90.91, f"attendu 90.91, obtenu {value}"

    # 10 s en marche puis 10 s en défaut → 50 % (l'état long pèse plus que l'instant).
    value = reliability.availability_pct(
        FakeSession([(0, "RUNNING"), (10_000, "FAULT")]), ["M1"], since_ms=0,
        now_ms=20_000)  # type: ignore[arg-type]
    assert value == 50.0, f"attendu 50.0, obtenu {value}"

    # Tout en marche : 100 %.
    assert reliability.availability_pct(
        FakeSession([(0, "RUNNING"), (10_000, "RUNNING"), (20_000, "RUNNING")]),
        ["M1"], since_ms=0, now_ms=30_000) == 100.0  # type: ignore[arg-type]

    # Le banc arrêté (état non exploitable) fait chuter la disponibilité.
    assert reliability.availability_pct(
        FakeSession([(0, "RUNNING"), (10_000, "STOPPED")]), ["M1"], since_ms=0,
        now_ms=20_000) == 50.0  # type: ignore[arg-type]

    # Base vide : None (l'appelant applique son repli).
    assert reliability.availability_pct(FakeSession([]), ["M1"], since_ms=0) is None  # type: ignore[arg-type]


def test_availability_caps_long_collection_gaps() -> None:
    """Une interruption de collecte ne doit pas être comptée comme du temps de marche."""
    from app.services import reliability

    class FakeSession:
        def __init__(self, samples):
            self._samples = samples

        def exec(self, _statement):
            class _Result:
                def __init__(self, rows):
                    self._rows = rows

                def all(self):
                    return self._rows

            return _Result(list(self._samples))

    # Deux points RUNNING espacés de 4 h (backend arrêté entre les deux).
    gap = 4 * 3_600_000
    value = reliability.availability_pct(
        FakeSession([(0, "RUNNING"), (gap, "RUNNING")]), ["M1"], since_ms=0,
        now_ms=gap)  # type: ignore[arg-type]
    assert value == 100.0, "les deux extrêmes sont exploitables : la disponibilité reste 100 %"
    # Mais chaque segment est plafonné (MAX_SEGMENT_MS) : la durée totale reste bornée,
    # la longue interruption n'est donc pas comptée comme du temps de marche mesuré.
    segments = reliability._segments(FakeSession([(0, "RUNNING"), (gap, "RUNNING")]), "M1", 0, now_ms=gap)  # type: ignore[arg-type]
    assert sum(duration for _, duration in segments) <= 2 * reliability.MAX_SEGMENT_MS


def test_downtime_hours_from_availability() -> None:
    """Le temps d'arrêt affiché doit se déduire de la disponibilité."""
    from app.services.reliability import downtime_hours

    assert downtime_hours(100.0, 24.0) == 0.0
    assert downtime_hours(97.5, 24.0) == 0.6
    assert downtime_hours(None, 24.0) is None


# =============================================================================
#  Exécution directe
# =============================================================================
def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  ✔ {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  ✘ {test.__name__} : {exc}")
        except Exception as exc:  # pragma: no cover - garde-fou
            failures += 1
            print(f"  ✘ {test.__name__} : erreur inattendue {exc!r}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests réussis")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
