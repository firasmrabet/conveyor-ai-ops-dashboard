"""API alertes, règles de seuil, maintenance prescriptive et ordres de travail."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, desc, func, select

from ..db import get_session
from ..models import Alert, AlertRule, Device, Intervention, ModelVersion, Motor, WorkOrder, now_ts_ms
from ..config import settings
from ..schemas import AlertUpdate, InterventionCreate, RuleCreate, WorkOrderCreate
from ..services.alerting import alert_engine
from ..services.common import logger, sign_payload

router = APIRouter(prefix="/api", tags=["alerts"])


# ==========================================================================
#  Alertes
# ==========================================================================
@router.get("/alerts", summary="Liste filtrée des alertes (tri, recherche, pagination)")
def list_alerts(
    motor_id: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    type: Optional[str] = None,
    search: Optional[str] = None,
    hours: int = Query(168, ge=1, le=8760),
    limit: int = Query(50, le=500),
    offset: int = 0,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    query = select(Alert).where(Alert.ts >= now_ts_ms() - hours * 3600_000)
    if motor_id:
        query = query.where(Alert.motor_id == motor_id)
    if severity:
        query = query.where(Alert.severity == severity)
    if status:
        query = query.where(Alert.status == status)
    if type:
        query = query.where(Alert.type == type)
    if search:
        query = query.where(Alert.message.contains(search))

    total = len(session.exec(query).all())
    rows = session.exec(query.order_by(desc(Alert.ts)).offset(offset).limit(limit)).all()
    counts = {
        "NEW": len(session.exec(select(Alert).where(Alert.status == "NEW")).all()),
        "ACKNOWLEDGED": len(session.exec(select(Alert).where(Alert.status == "ACKNOWLEDGED")).all()),
        "RESOLVED": len(session.exec(select(Alert).where(Alert.status == "RESOLVED")).all()),
    }
    severity_counts = {
        sev: len(session.exec(select(Alert).where(Alert.severity == sev)).all())
        for sev in ("INFO", "WARNING", "CRITICAL")
    }
    return {"total": total, "offset": offset, "limit": limit, "counts": counts,
            "severity_counts": severity_counts, "items": [a.model_dump() for a in rows]}


@router.get("/alerts/live", summary="Flux mémoire des dernières alertes (temps réel)")
def live_alerts(limit: int = 30) -> List[Dict[str, Any]]:
    from ..realtime import hub

    return list(reversed(hub.alerts.snapshot(limit=limit)))


@router.patch("/alerts/{alert_id}", summary="Acquitter / assigner / commenter une alerte")
async def update_alert(alert_id: int, payload: AlertUpdate,
                       session: Session = Depends(get_session)) -> Dict[str, Any]:
    alert = session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(404, "Alerte introuvable")
    if payload.status:
        alert.status = payload.status
        if payload.status == "ACKNOWLEDGED":
            alert.acknowledged_at = now_ts_ms()
            alert.acknowledged_by = payload.assigned_to or "operator"
        if payload.status == "RESOLVED":
            alert.resolved_at = now_ts_ms()
    if payload.assigned_to is not None:
        alert.assigned_to = payload.assigned_to
    if payload.comment is not None:
        alert.comment = payload.comment
    session.add(alert)
    session.commit()
    session.refresh(alert)
    updated = alert.model_dump()
    from ..realtime import hub
    await hub.broadcast({"type": "alert_update", "data": updated})
    return updated


@router.post("/alerts/{alert_id}/work-order", status_code=201,
             summary="Créer un ordre de travail depuis une alerte")
def alert_to_work_order(alert_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    alert = session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(404, "Alerte introuvable")
    order = WorkOrder(
        motor_id=alert.motor_id, title=f"Intervention suite à : {alert.message[:60]}",
        description=alert.message, priority="CRITICAL" if alert.severity == "CRITICAL" else "HIGH",
        source="RULE", alert_id=alert.id,
        parts=[], estimated_hours=1.0, due_in_days=2.0 if alert.severity == "CRITICAL" else 5.0,
    )
    session.add(order)
    alert.work_order_id = None
    session.commit()
    session.refresh(order)
    alert.work_order_id = order.id
    session.add(alert)
    session.commit()
    return order.model_dump()


@router.delete("/alerts/{alert_id}", summary="Supprimer une alerte")
def delete_alert(alert_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    alert = session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(404, "Alerte introuvable")
    session.delete(alert)
    session.commit()
    return {"deleted": alert_id}


# ==========================================================================
#  Règles d'alerte
# ==========================================================================
@router.get("/rules", summary="Liste des règles de seuil")
def list_rules(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    rules = session.exec(select(AlertRule)).all()
    return [r.model_dump() for r in rules]


@router.post("/rules", status_code=201, summary="Créer une règle de seuil")
def create_rule(payload: RuleCreate, session: Session = Depends(get_session)) -> Dict[str, Any]:
    rule = AlertRule(**payload.model_dump())
    session.add(rule)
    session.commit()
    session.refresh(rule)
    alert_engine.load_rules()
    logger.info("Règle créée : %s (%s %s %s)", rule.name, rule.metric, rule.operator, rule.threshold)
    return rule.model_dump()


@router.put("/rules/{rule_id}", summary="Modifier une règle")
def update_rule(rule_id: int, payload: RuleCreate, session: Session = Depends(get_session)) -> Dict[str, Any]:
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(404, "Règle introuvable")
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    alert_engine.load_rules()
    return rule.model_dump()


@router.patch("/rules/{rule_id}/toggle", summary="Activer/désactiver une règle")
def toggle_rule(rule_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(404, "Règle introuvable")
    rule.enabled = not rule.enabled
    session.add(rule)
    session.commit()
    alert_engine.load_rules()
    return {"id": rule.id, "enabled": rule.enabled}


@router.delete("/rules/{rule_id}", summary="Supprimer une règle")
def delete_rule(rule_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(404, "Règle introuvable")
    session.delete(rule)
    session.commit()
    alert_engine.load_rules()
    return {"deleted": rule_id}


# ==========================================================================
#  Notifications
# ==========================================================================
@router.get("/notifications/log", summary="Journal des notifications envoyées")
def notification_log(limit: int = Query(50, le=200)) -> Dict[str, Any]:
    return {
        "items": alert_engine.notification_log[-limit:],
        "channels": {
            "inapp": True,
            "email": {"enabled": settings.alert_email_enabled, "smtp": settings.smtp_host or None},
            "webhook": {"enabled": settings.alert_webhook_enabled, "url": settings.webhook_url or None},
            "sound": settings.alert_sound_enabled,
        },
    }


@router.post("/notifications/test/{channel}", summary="Test d'envoi sur un canal")
async def test_notification(channel: str) -> Dict[str, Any]:
    if channel not in ("inapp", "email", "webhook"):
        raise HTTPException(400, "Canal invalide (inapp, email, webhook)")
    result = await alert_engine.test_channel(channel)
    return {"channel": channel, "result": result}


# ==========================================================================
#  Maintenance prescriptive / ordres de travail / interventions
# ==========================================================================
@router.get("/maintenance/prescriptions", summary="Recommandations de maintenance générées")
def prescriptions() -> List[Dict[str, Any]]:
    return alert_engine.prescriptions()


@router.get("/maintenance/work-orders", summary="Tous les ordres de travail")
def list_work_orders(status: Optional[str] = None, motor_id: Optional[str] = None,
                     session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    query = select(WorkOrder)
    if status:
        query = query.where(WorkOrder.status == status)
    if motor_id:
        query = query.where(WorkOrder.motor_id == motor_id)
    rows = session.exec(query.order_by(desc(WorkOrder.created_at))).all()
    return [r.model_dump() for r in rows]


@router.post("/maintenance/work-orders", status_code=201, summary="Créer un ordre de travail")
def create_work_order(payload: WorkOrderCreate, session: Session = Depends(get_session)) -> Dict[str, Any]:
    order = WorkOrder(**payload.model_dump())
    session.add(order)
    session.commit()
    session.refresh(order)
    return order.model_dump()


@router.patch("/maintenance/work-orders/{order_id}", summary="Mettre à jour un ordre de travail")
def update_work_order(order_id: int, status: Optional[str] = None, assigned_to: Optional[str] = None,
                      session: Session = Depends(get_session)) -> Dict[str, Any]:
    order = session.get(WorkOrder, order_id)
    if not order:
        raise HTTPException(404, "Ordre de travail introuvable")
    if status:
        if status not in ("OPEN", "PLANNED", "IN_PROGRESS", "DONE"):
            raise HTTPException(400, "Statut invalide")
        order.status = status
    if assigned_to is not None:
        order.assigned_to = assigned_to
    session.add(order)
    session.commit()
    session.refresh(order)
    return order.model_dump()


@router.get("/maintenance/interventions", summary="Journal complet des interventions")
def all_interventions(limit: int = Query(100, le=1000), session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    rows = session.exec(select(Intervention).order_by(desc(Intervention.ts)).limit(limit)).all()
    return [r.model_dump() for r in rows]


@router.post("/maintenance/interventions", status_code=201, summary="Enregistrer une intervention")
def create_intervention(payload: InterventionCreate, session: Session = Depends(get_session)) -> Dict[str, Any]:
    ts = now_ts_ms()
    intervention = Intervention(ts=ts, **payload.model_dump())
    intervention.signature = sign_payload({"ts": ts, "motor_id": payload.motor_id,
                                           "action": payload.action, "technician": payload.technician})
    session.add(intervention)
    motor = session.get(Motor, payload.motor_id)
    if motor:
        motor.maintenance_count += 1
        motor.running_hours = 0.0
        session.add(motor)
    session.commit()
    session.refresh(intervention)
    return intervention.model_dump()


@router.get("/maintenance/stats", summary="Statistiques de maintenance (MTBF/MTTR/coûts)")
def maintenance_stats(session: Session = Depends(get_session)) -> Dict[str, Any]:
    interventions = session.exec(select(Intervention)).all()
    orders = session.exec(select(WorkOrder)).all()
    return {
        "total_interventions": len(interventions),
        "open_orders": len([o for o in orders if o.status in ("OPEN", "PLANNED", "IN_PROGRESS")]),
        "done_orders": len([o for o in orders if o.status == "DONE"]),
        "total_cost_eur": round(sum(i.cost_eur for i in interventions), 2),
        "total_hours": round(sum(i.duration_min for i in interventions) / 60.0, 2),
        "avg_duration_min": round(sum(i.duration_min for i in interventions) / max(len(interventions), 1), 1),
        "by_motor": {
            mid: {
                "interventions": len([i for i in interventions if i.motor_id == mid]),
                "cost_eur": round(sum(i.cost_eur for i in interventions if i.motor_id == mid), 2),
            } for mid in ("M1", "M2")
        },
        "parts_used": _parts_summary(interventions),
    }


def _parts_summary(interventions: List[Intervention]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for intervention in interventions:
        for part in intervention.parts_used:
            counts[part] = counts.get(part, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:10])
