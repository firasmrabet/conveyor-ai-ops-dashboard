"""Moteur d'alertes : règles seuils (hystérésis, durée, anti-spam) + alertes IA.

Canaux de notification : in-app (WebSocket) + toast/son côté front, e-mail SMTP,
webhook (Slack/Discord/Telegram générique). Tout est activable via .env.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import httpx
from sqlmodel import select

from ..config import settings
from ..db import session_scope
from ..models import Alert, AlertRule, Motor, WorkOrder
from ..realtime import hub
from .common import compare_metric, logger, rule_still_breached

SEVERITY_ORDER = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}

# Confiance minimale attendue d'un diagnostic de classe pour être publié en alerte.
# Affichée dans le journal d'alertes (colonne « seuil ») : elle rend le message lisible,
# la décision réelle restant prise par `_smooth()` + verrouillage 2 coups du service IA.
CLASS_CONFIDENCE_THRESHOLD = 0.25

# Rôles par défaut de maintenance (maintenance prescriptive)
PRESCRIPTIVE_MAP: Dict[str, Dict[str, Any]] = {
    "BEARING_FAULT": {
        "title": "Remplacer le roulement",
        "action": "Lubrifier puis remplacer le roulement (type 608-2RS)",
        "priority": "HIGH", "estimated_hours": 1.5, "due_in_days": 5,
        "parts": ["Roulement 608-2RS", "Graisse lithium NLGI-2"],
    },
    "UNBALANCE": {
        "title": "Rééquilibrer la poulie",
        "action": "Contrôler le balourd de la poulie et resserrer les vis de fixation",
        "priority": "MEDIUM", "estimated_hours": 0.8, "due_in_days": 7,
        "parts": ["Rondelle frein Ø5", "Loctite 243"],
    },
    "MISALIGNMENT": {
        "title": "Réaligner l'arbre moteur",
        "action": "Réaligner l'accouplement (tolérances < 0,05 mm) et vérifier le serrage du support",
        "priority": "HIGH", "estimated_hours": 1.2, "due_in_days": 4,
        "parts": ["Accouplement flexible Ø5→Ø8"],
    },
    "BELT_SLIP": {
        "title": "Retendre la bande",
        "action": "Vérifier la tension de la bande et la propreté des poulies (glissement détecté)",
        "priority": "HIGH", "estimated_hours": 0.6, "due_in_days": 2,
        "parts": ["Kit de tension de bande"],
    },
    "OVERLOAD": {
        "title": "Réduire la charge transportée",
        "action": "Limiter la charge par cycle et vérifier l'absence de frottement mécanique",
        "priority": "MEDIUM", "estimated_hours": 0.4, "due_in_days": 3,
        "parts": [],
    },
    "OVERHEATING": {
        "title": "Améliorer la dissipation thermique",
        "action": "Prolonger la pause entre cycles, vérifier le driver (courant de maintien) et ventiler le banc",
        "priority": "CRITICAL", "estimated_hours": 0.5, "due_in_days": 1,
        "parts": ["Dissipateur aluminium", "Ventilateur 40 mm"],
    },
    "HEALTHY": {
        "title": "Contrôle de routine",
        "action": "Poursuivre la surveillance, aucun défaut identifié",
        "priority": "LOW", "estimated_hours": 0.2, "due_in_days": 30, "parts": [],
    },
}


class AlertEngine:
    """Évalue les règles à chaque nouvelle donnée et publie les alertes."""

    def __init__(self) -> None:
        self.rules: List[AlertRule] = []
        self._state: Dict[str, Dict[str, Any]] = {}     # clé -> {since, last_fired, fired}
        self._last_ai_class: Dict[str, str] = {}
        self._last_ai_ts: Dict[str, float] = {"M1": 0.0, "M2": 0.0}
        # Machine à états anti-flapping : {motor: {"candidate", "count", "last_alert_ts"}}
        self._ai_state: Dict[str, Dict[str, Any]] = {}
        self._anomaly_state: Dict[str, Dict[str, Any]] = {}
        self.notification_log: List[Dict[str, Any]] = []
        self.loaded_at: float = 0.0

    # ------------------------------------------------------------------
    def load_rules(self) -> List[AlertRule]:
        with session_scope() as session:
            rules = session.exec(select(AlertRule).where(AlertRule.enabled == True)).all()  # noqa: E712
            self.rules = list(rules)
        self.loaded_at = time.time()
        logger.info("%d règles d'alerte actives", len(self.rules))
        return self.rules

    # ------------------------------------------------------------------
    async def evaluate(self, motor_id: str, metrics: Dict[str, float], ts_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        """Évalue les règles pour les métriques fournies.

        `metrics` contient : vibration_rms, bearing_temp, motor_temp, current_rms,
        health_index, anomaly_score, rpm, spl_db, belt_speed_error...
        """
        ts = ts_ms or int(time.time() * 1000)
        emitted: List[Dict[str, Any]] = []
        for rule in self.rules:
            if rule.motor_id not in ("*", motor_id):
                continue
            if rule.metric not in metrics:
                continue
            value = float(metrics[rule.metric])
            key = f"{rule.id}:{motor_id}"
            state = self._state.setdefault(key, {"since": None, "last_fired": 0.0})
            breach = compare_metric(value, rule.operator, rule.threshold)
            # Hystérésis : une alerte déjà active ne se referme qu'une fois la mesure
            # sortie de la bande morte (direction dépendante de l'opérateur, cf.
            # common.rule_still_breached — le signe inverse maintenait les alertes
            # « chute de vitesse » et « santé basse » actives indéfiniment).
            if not breach and state.get("active"):
                breach = rule_still_breached(value, rule.operator, rule.threshold, rule.hysteresis)
            if not breach:
                state["since"] = None
                state["active"] = False
                continue

            now = time.time()
            if state["since"] is None:
                state["since"] = now
            elapsed = now - state["since"]
            if elapsed < rule.duration_s:
                continue
            if now - state["last_fired"] < rule.debounce_s:
                continue
            state["active"] = True
            state["last_fired"] = now
            emitted.append(await self.raise_alert(
                motor_id=motor_id, metric=rule.metric, value=value, threshold=rule.threshold,
                operator=rule.operator, severity=rule.severity, message=rule.name,
                alert_type="THRESHOLD", origin="RULES", rule_id=rule.id, ts=ts,
                channels=list(rule.channels or ["inapp"]),
            ))
        return emitted

    # ------------------------------------------------------------------
    async def evaluate_ai(self, motor_id: str, inference: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Alertes issues de l'IA — avec confirmation et temporisation.

        Un changement de diagnostic n'est publié qu'après `AI_CONFIRMATIONS` inférences
        consécutives concordantes, et au maximum une fois toutes les `AI_DEBOUNCE_S`
        secondes par moteur : cela élimine le clignotement à la frontière de décision
        (le classifieur oscille naturellement autour de ses seuils).
        """
        emitted: List[Dict[str, Any]] = []
        now = time.time()
        ts = int(inference.get("ts", now * 1000))
        fault_class = inference.get("fault_class", "HEALTHY")
        anomaly = float(inference.get("anomaly_score", 0.0))
        health = float(inference.get("health_index", 100.0))

        # ---------------- 1. Anomalie persistante ----------------
        anomaly_state = self._anomaly_state.setdefault(
            motor_id, {"streak": 0, "last_alert_ts": 0.0})
        if inference.get("is_anomaly"):
            anomaly_state["streak"] += 1
        else:
            anomaly_state["streak"] = 0
        if (anomaly_state["streak"] >= settings.anomaly_confirmations
                and now - anomaly_state["last_alert_ts"] > settings.anomaly_debounce_s):
            anomaly_state["last_alert_ts"] = now
            self._last_ai_ts[motor_id] = now
            emitted.append(await self.raise_alert(
                motor_id=motor_id, metric="anomaly_score", value=anomaly, threshold=0.6,
                operator=">", severity="WARNING" if anomaly < 0.8 else "CRITICAL",
                message=f"Anomalie vibratoire persistante sur {motor_id} (score {anomaly:.2f})",
                alert_type="ANOMALY", origin="ML", ts=ts,
                context={"health_index": health, "streak": anomaly_state["streak"],
                         "explanation": inference.get("explanation", [])[:3]},
            ))

        # ---------------- 2. Changement de diagnostic confirmé ----------------
        state = self._ai_state.setdefault(motor_id, {"candidate": None, "count": 0,
                                                     "last_alert_ts": 0.0})
        confirmed = self._last_ai_class.get(motor_id, "HEALTHY")
        if fault_class != confirmed:
            if state["candidate"] == fault_class:
                state["count"] += 1
            else:
                state["candidate"] = fault_class
                state["count"] = 1
            stable_margin = state["count"] >= settings.ai_confirmations
            cooled_down = now - state["last_alert_ts"] > settings.ai_debounce_s
            if stable_margin and cooled_down:
                self._last_ai_class[motor_id] = fault_class
                state.update({"last_alert_ts": now, "count": 0, "candidate": None})
                if fault_class == "HEALTHY":
                    # Alerte de retour à la normale : on trace la santé face au seuil d'alerte
                    emitted.append(await self.raise_alert(
                        motor_id=motor_id, metric="health_index",
                        value=round(float(health), 2), threshold=85.0, operator=">",
                        severity="INFO",
                        message=f"{motor_id} revenu à un état sain (santé {health:.0f}/100)",
                        alert_type="AI_CLASS", origin="ML", ts=ts,
                        context={"health_index": health, "rul_hours": inference.get("rul_hours")},
                    ))
                else:
                    prescription = PRESCRIPTIVE_MAP.get(fault_class, {})
                    critical = fault_class in ("OVERHEATING", "BELT_SLIP", "OVERLOAD") or health < 40
                    # Confiance réellement rapportée : probabilité de la classe retenue.
                    # Le seuil affiché est la confiance minimale exigée pour publier un diagnostic.
                    probabilities = inference.get("fault_probabilities", {}) or {}
                    confidence = float(probabilities.get(fault_class, 0.0)
                                       or probabilities.get(fault_class.lower(), 0.0)
                                       or probabilities.get(fault_class.upper(), 0.0))
                    if confidence <= 0.0:  # modèle sans probabilités explicites
                        confidence = 1.0
                    label = fault_class.replace("_", " ").lower()
                    # L'ordre de travail est créé dès la confirmation du diagnostic :
                    # la boucle de maintenance est immédiatement traçable dans l'interface
                    # (le passage périodique du planificateur reste un filet de sécurité).
                    await asyncio.to_thread(self._ensure_work_order, motor_id, fault_class, prescription,
                                            inference)
                    emitted.append(await self.raise_alert(
                        motor_id=motor_id, metric="fault_confidence",
                        value=round(confidence, 4), threshold=CLASS_CONFIDENCE_THRESHOLD, operator=">",
                        severity="CRITICAL" if critical else "WARNING",
                        message=(f"Diagnostic IA : {label} détecté sur {motor_id} "
                                 f"(confiance {confidence * 100:.0f} %)"),
                        alert_type="AI_CLASS", origin="ML", ts=ts,
                        context={
                            "fault_class": fault_class,
                            "confidence": round(confidence, 4),
                            "fault_probabilities": probabilities,
                            "explanation": inference.get("explanation", [])[:4],
                            "recommendation": prescription.get("action", ""),
                            "rul_hours": inference.get("rul_hours"),
                            "health_index": health,
                        },
                    ))
        else:
            state.update({"count": 0, "candidate": None})
        return emitted


    # ------------------------------------------------------------------
    def _ensure_work_order(self, motor_id: str, fault_class: str, prescription: Dict[str, Any],
                           inference: Dict[str, Any]) -> None:
        """Crée l'ordre de travail prescriptif s'il n'existe pas déjà (idempotent).

        Un seul ordre ouvert par couple (moteur, intitulé) : un défaut qui persiste ne
        génère pas une avalanche d'ordres, et l'ordre existant n'est pas dupliqué.
        """
        if not prescription or fault_class == "HEALTHY":
            return
        title = str(prescription.get("title", "")).strip()
        if not title:
            return
        try:
            with session_scope() as session:
                existing = session.exec(
                    select(WorkOrder).where(
                        WorkOrder.motor_id == motor_id,
                        WorkOrder.title == title,
                        WorkOrder.status != "DONE",
                    )
                ).first()
                if existing:
                    return
                health = float(inference.get("health_index", 100.0))
                rul = float(inference.get("rul_hours", 0.0))
                session.add(WorkOrder(
                    motor_id=motor_id,
                    title=title,
                    description=(f"{prescription.get('action', '')} — diagnostic IA : {fault_class} "
                                 f"(santé {health:.0f}/100, RUL estimé {rul:.0f} h)."),
                    priority=str(prescription.get("priority", "MEDIUM")),
                    source="AI",
                    estimated_hours=float(prescription.get("estimated_hours", 1.0)),
                    parts=list(prescription.get("parts", [])),
                    due_in_days=float(prescription.get("due_in_days", 7)),
                ))
            logger.info("Ordre de travail prescriptif créé : %s / %s", motor_id, title)
        except Exception as exc:  # pragma: no cover - la maintenance ne doit jamais bloquer l'ingestion
            logger.warning("Création de l'ordre de travail ignorée (%s) : %s", motor_id, exc)

    # ------------------------------------------------------------------
    async def raise_alert(self, *, motor_id: str, metric: str, value: float, threshold: float,
                          operator: str, severity: str, message: str, alert_type: str,
                          origin: str, rule_id: Optional[int] = None, ts: Optional[int] = None,
                          context: Optional[Dict[str, Any]] = None,
                          channels: Optional[List[str]] = None) -> Dict[str, Any]:
        """Crée l'alerte en base, la diffuse et déclenche les notifications."""
        ts = ts or int(time.time() * 1000)
        channels = channels or ["inapp"]
        with session_scope() as session:
            alert = Alert(
                ts=ts, motor_id=motor_id, severity=severity, type=alert_type, origin=origin,
                metric=metric, value=round(float(value), 4), threshold=round(float(threshold), 4),
                message=message, status="NEW", rule_id=rule_id, context=context or {},
            )
            session.add(alert)
            session.flush()
            payload = {
                "id": alert.id, "ts": ts, "motor_id": motor_id, "severity": severity,
                "type": alert_type, "origin": origin, "metric": metric,
                "value": alert.value, "threshold": alert.threshold, "message": message,
                "status": "NEW", "context": context or {},
            }
        hub.alerts.push_nowait(payload)
        hub.tick_counters(n_alerts=1)
        await hub.broadcast({"type": "alert", "data": payload})
        logger.warning("ALERTE [%s] %s — %s (%.3f %s %.3f)", severity, motor_id, message,
                       value, operator, threshold)
        await self.notify(payload, channels)
        return payload

    # ------------------------------------------------------------------
    async def notify(self, alert: Dict[str, Any], channels: List[str]) -> None:
        """Diffuse l'alerte sur les canaux configurés. Chaque envoi est journalisé."""
        title = f"[{alert['severity']}] {alert['motor_id']} — {alert['message']}"
        body = (f"Métrique : {alert['metric']}\nValeur : {alert['value']} "
                f"(seuil {alert['threshold']})\nTimestamp : {alert['ts']}")
        for channel in channels:
            entry = {"ts": int(time.time() * 1000), "channel": channel, "alert_id": alert["id"],
                     "title": title, "state": "skipped", "detail": ""}
            try:
                if channel == "inapp":
                    entry["state"] = "delivered"      # déjà poussé en WebSocket
                elif channel == "webhook":
                    entry.update(await self._send_webhook(title, body))
                elif channel == "email":
                    entry.update(await self._send_email(title, body))
                else:
                    entry["detail"] = f"canal inconnu : {channel}"
            except Exception as exc:  # pragma: no cover
                entry["state"] = "error"
                entry["detail"] = str(exc)
            self.notification_log.append(entry)
        self.notification_log[:] = self.notification_log[-200:]
        await hub.broadcast({"type": "notification_log", "data": self.notification_log[-10:]})

    async def _send_webhook(self, title: str, body: str) -> Dict[str, str]:
        if not settings.alert_webhook_enabled or not settings.webhook_url:
            return {"state": "disabled", "detail": "webhook désactivé (.env : ALERT_WEBHOOK_ENABLED/WEBHOOK_URL)"}
        payload = {
            "text": f"{title}\n{body}",
            "content": f"**{title}**\n{body}",      # Discord / Slack
        }
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(settings.webhook_url, json=payload)
        return {"state": "delivered" if resp.status_code < 300 else "error",
                "detail": f"HTTP {resp.status_code}"}

    async def _send_email(self, title: str, body: str) -> Dict[str, str]:
        if not settings.alert_email_enabled or not settings.smtp_host:
            return {"state": "disabled", "detail": "email désactivé (.env : ALERT_EMAIL_ENABLED/SMTP_HOST)"}
        import smtplib
        from email.mime.text import MIMEText

        def _send() -> None:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = title
            msg["From"] = settings.smtp_from
            msg["To"] = settings.smtp_from
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=8) as smtp:
                if settings.smtp_user:
                    smtp.starttls()
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)

        await asyncio.to_thread(_send)
        return {"state": "delivered", "detail": f"SMTP {settings.smtp_host}"}

    async def test_channel(self, channel: str) -> Dict[str, Any]:
        """Test d'envoi (bouton « Tester » de la page Alertes)."""
        fake = {"id": 0, "ts": int(time.time() * 1000), "motor_id": "M1", "severity": "INFO",
                "message": "Message de test depuis Conveyor AI Ops", "metric": "self_test",
                "value": 1.0, "threshold": 0.0, "type": "SYSTEM", "status": "NEW"}
        await self.notify(fake, [channel])
        return self.notification_log[-1] if self.notification_log else {}

    # ------------------------------------------------------------------
    def prescriptions(self) -> List[Dict[str, Any]]:
        """Maintenance prescriptive : recommandations à partir des derniers diagnostics."""
        out: List[Dict[str, Any]] = []
        for motor_id in ("M1", "M2"):
            inference = hub.inference[motor_id].last()
            if not inference:
                continue
            fault_class = inference.get("fault_class", "HEALTHY")
            preset = PRESCRIPTIVE_MAP.get(fault_class, PRESCRIPTIVE_MAP["HEALTHY"])
            health = float(inference.get("health_index", 100.0))
            rul = float(inference.get("rul_hours", 0.0))
            urgency = max(1.0, min(30.0, rul / 24.0)) if rul > 0 else 1.0
            out.append({
                "motor_id": motor_id,
                "fault_class": fault_class,
                "title": preset["title"],
                "action": preset["action"],
                "priority": preset["priority"],
                "estimated_hours": preset["estimated_hours"],
                "due_in_days": round(min(preset["due_in_days"], urgency), 1),
                "parts": preset["parts"],
                "health_index": health,
                "rul_hours": rul,
                "confidence": round(min(0.98, 0.55 + (100 - health) / 200), 3),
                "rationale": self._rationale(inference),
            })
        out.sort(key=lambda p: (SEVERITY_ORDER.get(p["priority"], 0), p["health_index"]))
        return out

    @staticmethod
    def _rationale(inference: Dict[str, Any]) -> str:
        """Texte explicatif généré à partir des contributions XAI."""
        explanation = inference.get("explanation", [])
        if not explanation:
            return "Aucun écart significatif par rapport à la signature de référence."
        parts = [f"{e.get('label', e.get('feature'))} (contribution {float(e.get('contribution', 0)) * 100:.0f} %)"
                 for e in explanation[:3]]
        fault = str(inference.get("fault_class", "HEALTHY")).replace("_", " ").lower()
        return (f"Signature dominante : {', '.join(parts)}. "
                f"Diagnostic {fault} avec un indice de santé de {inference.get('health_index')} "
                f"et un RUL estimé à {inference.get('rul_hours')} h.")


alert_engine = AlertEngine()
