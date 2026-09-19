"""Tâches périodiques (APScheduler) : agrégats, rétention, MTBF/MTTR, supervision devices.

Si APScheduler n'est pas installé, un planificateur asyncio de secours prend le
relais : l'application ne dépend d'aucun paquet optionnel pour démarrer.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlmodel import delete, func, select

from ..config import settings
from ..db import session_scope
from ..models import (Alert, Device, HealthSnapshot, HourlyAggregate, InferenceRecord,
                      Intervention, TelemetryRecord, WorkOrder)
from ..realtime import hub
from .alerting import alert_engine, PRESCRIPTIVE_MAP
from .common import logger
from .ml_service import ml_service

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore

    HAS_APSCHEDULER = True
except Exception:  # pragma: no cover
    HAS_APSCHEDULER = False


# ==========================================================================
#  Tâches
# ==========================================================================
async def task_compute_rates() -> None:
    """Calcule les débits (msg/s, ko/s) diffusés au header."""
    hub.compute_rates()
    if hub.clients:
        await hub.broadcast({"type": "rates", "data": hub.rates})


async def task_hourly_aggregates() -> None:
    """Agrège la dernière heure glissante par moteur (rétention longue)."""

    def _work() -> int:
        now = int(time.time() * 1000)
        bucket = now - (now % 3_600_000)
        written = 0
        with session_scope() as session:
            for motor_id in ("M1", "M2"):
                rows = session.exec(
                    select(HealthSnapshot).where(
                        HealthSnapshot.motor_id == motor_id,
                        HealthSnapshot.ts >= bucket - 3_600_000,
                        HealthSnapshot.ts < bucket,
                    )
                ).all()
                if not rows:
                    continue
                n = len(rows)
                existing = session.exec(
                    select(HourlyAggregate).where(
                        HourlyAggregate.motor_id == motor_id, HourlyAggregate.bucket_ts == bucket - 3_600_000
                    )
                ).first()
                if existing:
                    existing.avg_health = sum(r.health_index for r in rows) / n
                    existing.min_health = min(r.health_index for r in rows)
                    existing.avg_vibration_rms = sum(r.vibration_rms for r in rows) / n
                    existing.max_vibration_rms = max(r.vibration_rms for r in rows)
                    existing.avg_current_rms = sum(r.current_rms for r in rows) / n
                    existing.avg_temp_bearing = sum(r.temp_bearing_c for r in rows) / n
                    existing.max_temp_bearing = max(r.temp_bearing_c for r in rows)
                    existing.avg_rpm = sum(r.rpm for r in rows) / n
                    existing.anomaly_count = sum(1 for r in rows if r.anomaly_score > 0.55)
                    existing.sample_count = n
                    session.add(existing)
                else:
                    session.add(HourlyAggregate(
                        bucket_ts=bucket - 3_600_000, motor_id=motor_id,
                        avg_health=sum(r.health_index for r in rows) / n,
                        min_health=min(r.health_index for r in rows),
                        avg_vibration_rms=sum(r.vibration_rms for r in rows) / n,
                        max_vibration_rms=max(r.vibration_rms for r in rows),
                        avg_current_rms=sum(r.current_rms for r in rows) / n,
                        avg_temp_bearing=sum(r.temp_bearing_c for r in rows) / n,
                        max_temp_bearing=max(r.temp_bearing_c for r in rows),
                        avg_rpm=sum(r.rpm for r in rows) / n,
                        anomaly_count=sum(1 for r in rows if r.anomaly_score > 0.55),
                        sample_count=n,
                    ))
                written += 1
        return written

    try:
        count = await asyncio.to_thread(_work)
        if count:
            logger.info("Agrégats horaires écrits pour %d moteur(s)", count)
    except Exception as exc:  # pragma: no cover
        logger.error("Agrégation horaire échouée : %s", exc)


async def task_retention() -> None:
    """Purge les données au-delà de la rétention configurée."""

    def _work() -> Dict[str, int]:
        now = int(time.time() * 1000)
        cutoff_raw = now - settings.raw_retention_days * 86_400_000
        cutoff_feat = now - settings.feature_retention_days * 86_400_000
        deleted = {}
        with session_scope() as session:
            res = session.exec(delete(TelemetryRecord).where(TelemetryRecord.ts < cutoff_raw))
            deleted["telemetry"] = res.rowcount or 0
            res = session.exec(delete(InferenceRecord).where(InferenceRecord.ts < cutoff_feat))
            deleted["inference"] = res.rowcount or 0
            res = session.exec(delete(HealthSnapshot).where(HealthSnapshot.ts < cutoff_feat))
            deleted["health_snapshot"] = res.rowcount or 0
        return deleted

    try:
        deleted = await asyncio.to_thread(_work)
        if any(deleted.values()):
            logger.info("Rétention : %s", deleted)
    except Exception as exc:  # pragma: no cover
        logger.error("Purge de rétention échouée : %s", exc)


async def task_device_supervision() -> None:
    """Détecte les devices hors ligne + simule les métriques réseau."""

    def _work() -> List[Dict[str, Any]]:
        now = int(time.time() * 1000)
        offline: List[Dict[str, Any]] = []
        never_seen: List[str] = []
        with session_scope() as session:
            devices = session.exec(select(Device)).all()
            for device in devices:
                # Le topic de heartbeat arrive par deux chemins : la base (durable) et
                # la mémoire du concentrateur (immédiate). On retient l'horodatage le
                # plus FRAIS : si une écriture SQLite a été retardée par un verrou, la
                # carte n'est pas déclarée hors ligne à tort alors qu'elle publie.
                memory = hub.devices.get(device.device_id) or {}
                freshest = max(device.last_heartbeat or 0, int(memory.get("received_at", 0) or 0))
                age_s = (now - freshest) / 1000.0
                state = "ONLINE" if age_s < 15 else ("DEGRADED" if age_s < 45 else "OFFLINE")
                if state != device.status:
                    device.status = state
                    session.add(device)
                if state != "OFFLINE":
                    continue
                # Aucun heartbeat depuis le démarrage du backend : la carte n'est pas
                # « tombée », elle n'a simplement pas encore été branchée/connectée. On
                # affiche l'état OFFLINE dans l'interface sans générer d'alerte
                # critique — sinon chaque démarrage en MODE=HARDWARE déclenchait une
                # fausse alerte « passerelle hors ligne » avant la première trame.
                if not memory:
                    never_seen.append(device.device_id)
                    continue
                offline.append({"device_id": device.device_id, "age_s": round(age_s, 1),
                                "since_last": round((now - int(memory.get("received_at", 0))) / 1000.0, 1)})
        if never_seen:
            logger.info("Passerelle(s) non encore connectée(s) : %s (aucune alerte émise)", ", ".join(never_seen))
        return offline

    try:
        offline = await asyncio.to_thread(_work)
        for item in offline:
            if time.time() - getattr(task_device_supervision, "_last_alert", 0) > 300:
                task_device_supervision._last_alert = time.time()  # type: ignore[attr-defined]
                await alert_engine.raise_alert(
                    motor_id="SYSTEM", metric="device_offline", value=item["age_s"], threshold=15,
                    operator=">", severity="CRITICAL",
                    message=f"Passerelle {item['device_id']} hors ligne depuis {item['age_s']:.0f} s",
                    alert_type="DEVICE", origin="DEVICE",
                )
    except Exception as exc:  # pragma: no cover
        logger.error("Supervision devices échouée : %s", exc)


async def task_reliability_metrics() -> None:
    """Met à jour MTBF / MTTR et crée les ordres de travail prescriptifs."""

    def _work() -> Dict[str, Any]:
        now = int(time.time() * 1000)
        with session_scope() as session:
            stats: Dict[str, Any] = {}
            for motor_id in ("M1", "M2"):
                interventions = session.exec(
                    select(Intervention).where(Intervention.motor_id == motor_id)
                ).all()
                alerts = session.exec(select(Alert).where(Alert.motor_id == motor_id)).all()
                hours_span = max(1.0, (now - (min((a.ts for a in alerts), default=now)) / 1000 / 3600))
                failures = len([a for a in alerts if a.severity in ("CRITICAL",)])
                mttr = (sum(i.duration_min for i in interventions) / len(interventions) / 60.0) if interventions else 1.5
                stats[motor_id] = {
                    "mtbf_h": round(hours_span / max(failures, 1), 2),
                    "mttr_h": round(mttr, 2),
                    "failures": failures,
                    "interventions": len(interventions),
                    "alerts": len(alerts),
                }
            # Ordres de travail prescriptifs (un seul ouvert par moteur/défaut)
            for prescription in alert_engine.prescriptions():
                if prescription["fault_class"] == "HEALTHY":
                    continue
                existing = session.exec(
                    select(WorkOrder).where(
                        WorkOrder.motor_id == prescription["motor_id"],
                        WorkOrder.title == prescription["title"],
                        WorkOrder.status != "DONE",
                    )
                ).first()
                if not existing:
                    session.add(WorkOrder(
                        motor_id=prescription["motor_id"], title=prescription["title"],
                        description=prescription["rationale"], priority=prescription["priority"],
                        source="AI", estimated_hours=prescription["estimated_hours"],
                        parts=prescription["parts"], due_in_days=prescription["due_in_days"],
                    ))
                    logger.info("Ordre de travail prescriptif créé : %s / %s",
                                prescription["motor_id"], prescription["title"])
        return stats

    try:
        await asyncio.to_thread(_work)
    except Exception as exc:  # pragma: no cover
        logger.error("Calcul MTBF/MTTR échoué : %s", exc)


async def task_snapshot_metrics() -> None:
    """Diffuse périodiquement un résumé (KPI) pour les bandeaux du dashboard."""
    summary: Dict[str, Any] = {"ts": int(time.time() * 1000), "motors": {}}
    for motor_id in ("M1", "M2"):
        inference = hub.inference[motor_id].last() or {}
        telemetry = hub.telemetry[motor_id].last() or {}
        summary["motors"][motor_id] = {
            "health_index": inference.get("health_index", 100.0),
            "fault_class": inference.get("fault_class", "HEALTHY"),
            "rul_hours": inference.get("rul_hours", 0.0),
            "anomaly_score": inference.get("anomaly_score", 0.0),
            "rpm": telemetry.get("speed", {}).get("rpm", 0.0),
            "power_w": telemetry.get("current", {}).get("power_w", 0.0),
            "temp_bearing": telemetry.get("temperature", {}).get("bearing_c", 0.0),
            "wear": telemetry.get("_wear", 0.0),
        }
    summary["status"] = hub.status
    summary["rates"] = hub.rates
    summary["mqtt"] = {"status": "SIMULATION" if settings.is_simulation else "HARDWARE"}
    summary["model"] = ml_service.engine.model_info if ml_service.engine else {}
    await hub.broadcast({"type": "summary", "data": summary})


async def task_maintenance_tick() -> None:
    """Vérifie l'état des roulements par rapport au plan de maintenance."""
    with session_scope() as session:
        open_orders = session.exec(select(WorkOrder).where(WorkOrder.status == "OPEN")).all()
        overdue = [o for o in open_orders
                   if time.time() * 1000 - o.created_at > o.due_in_days * 86_400_000]
        for order in overdue:
            critical = HEALTH_CRITICAL_CHECK(order.motor_id)
            if critical:
                await alert_engine.raise_alert(
                    motor_id=order.motor_id, metric="work_order", value=order.due_in_days,
                    threshold=order.due_in_days, operator=">", severity="WARNING",
                    message=f"Ordre de travail en retard : {order.title}",
                    alert_type="SYSTEM", origin="RULES",
                    context={"work_order_id": order.id, "priority": order.priority},
                )


def HEALTH_CRITICAL_CHECK(motor_id: str) -> bool:
    inference = hub.inference[motor_id].last()
    if not inference:
        return True
    return float(inference.get("health_index", 100.0)) < 70


# ==========================================================================
#  Planificateur
# ==========================================================================
class FallbackScheduler:
    """Planificateur asyncio minimal (si APScheduler est absent)."""

    def __init__(self) -> None:
        self.jobs: List[Dict[str, Any]] = []
        self._tasks: List[asyncio.Task] = []

    def add(self, func: Any, seconds: float, job_id: str = "") -> None:
        self.jobs.append({"func": func, "seconds": seconds, "id": job_id or func.__name__})

    async def start(self) -> None:
        for job in self.jobs:
            self._tasks.append(asyncio.create_task(self._loop(job)))

    async def _loop(self, job: Dict[str, Any]) -> None:
        await asyncio.sleep(min(3.0, job["seconds"]))
        while True:
            try:
                await job["func"]()
            except Exception as exc:  # pragma: no cover
                logger.error("Tâche %s échouée : %s", job["id"], exc)
            await asyncio.sleep(job["seconds"])

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()


JOBS = [
    (task_compute_rates, 2.0, "rates"),
    (task_snapshot_metrics, 2.0, "snapshot"),
    (task_device_supervision, 10.0, "devices"),
    (task_reliability_metrics, 60.0, "reliability"),
    (task_hourly_aggregates, 300.0, "aggregates"),
    (task_retention, 3600.0, "retention"),
    (task_maintenance_tick, 120.0, "maintenance"),
]


class SchedulerService:
    def __init__(self) -> None:
        self.scheduler: Any = None
        self.fallback: Optional[FallbackScheduler] = None
        self.backend = "apscheduler" if HAS_APSCHEDULER else "asyncio-fallback"

    async def start(self) -> None:
        if HAS_APSCHEDULER:
            self.scheduler = AsyncIOScheduler(timezone="UTC")
            for func, seconds, job_id in JOBS:
                # `misfire_grace_time` : sous forte charge (test navigateur, export PDF,
                # conteneur limité en CPU), un tick peut accuser un léger retard sans
                # devoir être signalé comme manqué ; `coalesce` fusionne les rattrapages
                # et `max_instances=1` interdit l'exécution concurrente d'une même tâche.
                self.scheduler.add_job(func, "interval", seconds=seconds, id=job_id,
                                       max_instances=1, coalesce=True,
                                       misfire_grace_time=max(5, int(seconds)))
            self.scheduler.start()
            logger.info("APScheduler démarré : %d tâches", len(JOBS))
        else:
            self.fallback = FallbackScheduler()
            for func, seconds, job_id in JOBS:
                self.fallback.add(func, seconds, job_id)
            await self.fallback.start()
            logger.info("Planificateur asyncio de secours démarré : %d tâches", len(JOBS))

    async def shutdown(self) -> None:
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
        if self.fallback:
            await self.fallback.shutdown()


scheduler_service = SchedulerService()
