"""Génération du rapport PDF de maintenance (reportlab).

Contenu : synthèse exécutive, état des deux moteurs, courbes de santé,
diagnostics IA + XAI, alertes récentes, maintenance prescriptive, MTBF/MTTR.
Le PDF est généré en mémoire et renvoyé en streaming par l'API.
"""
from __future__ import annotations

import io
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlmodel import desc, select

from ..config import settings
from ..db import session_scope
from ..models import Alert, HealthSnapshot, Intervention, WorkOrder
from ..realtime import hub
from .alerting import alert_engine
from .common import logger

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

# Palette alignée sur le design system du frontend
CYAN = colors.HexColor("#00A8BF")
GREEN = colors.HexColor("#00A36C")
AMBER = colors.HexColor("#D99000")
RED = colors.HexColor("#D93636")
VIOLET = colors.HexColor("#5B45C9")
DARK = colors.HexColor("#0F1420")
GREY = colors.HexColor("#5A6478")
LIGHT = colors.HexColor("#F2F4F8")

SEVERITY_COLORS = {"INFO": CYAN, "WARNING": AMBER, "CRITICAL": RED}


def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=20, textColor=DARK, spaceAfter=2, alignment=TA_LEFT),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontSize=9.5, textColor=GREY,
                                   spaceAfter=10),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=12.5, textColor=DARK,
                             spaceBefore=12, spaceAfter=6),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9, leading=12.5, textColor=colors.HexColor("#22293A")),
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=7.6, textColor=GREY, leading=10),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=8, leading=10.5),
    }


def _health_color(value: float) -> colors.Color:
    if value >= 75:
        return GREEN
    if value >= 55:
        return AMBER
    return RED


def _line_chart(points: List[float], width: float = 460, height: float = 120, color: colors.Color = CYAN,
                title: str = "") -> Drawing:
    drawing = Drawing(width, height)
    if not points:
        drawing.add(String(6, height / 2, "Aucune donnée disponible pour la période",
                           fontSize=8, fillColor=GREY))
        return drawing
    data = points[-120:]
    chart = HorizontalLineChart()
    chart.x, chart.y = 8, 18
    chart.width, chart.height = width - 20, height - 34
    chart.data = [data]
    chart.lines[0].strokeColor = color
    chart.lines[0].strokeWidth = 1.4
    chart.joinedLines = 1
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 25
    chart.valueAxis.labels.fontSize = 6.5
    chart.categoryAxis.labels.fontSize = 6
    chart.categoryAxis.visibleTicks = 0
    chart.categoryAxis.labels.boxAnchor = "n"
    chart.categoryAxis.labels.dy = -2
    drawing.add(chart)
    if title:
        drawing.add(String(8, height - 9, title, fontSize=8, fillColor=DARK, fontName="Helvetica-Bold"))
    return drawing


def _bar_chart(values: List[float], labels: List[str], width: float = 460, height: float = 130,
               color: colors.Color = VIOLET, title: str = "", max_value: Optional[float] = None) -> Drawing:
    drawing = Drawing(width, height)
    if not values:
        drawing.add(String(6, height / 2, "Aucune donnée", fontSize=8, fillColor=GREY))
        return drawing
    chart = VerticalBarChart()
    chart.x, chart.y = 30, 28
    chart.width, chart.height = width - 60, height - 48
    chart.data = [values]
    chart.bars[0].fillColor = color
    chart.bars[0].strokeColor = None
    chart.barWidth = max(3, min(16, (chart.width / max(len(values), 1)) * 0.6))
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max_value or (max(values) * 1.25 or 1)
    chart.valueAxis.labels.fontSize = 6.5
    chart.categoryAxis.labels.fontSize = 6
    chart.categoryAxis.labels.angle = 25
    chart.categoryAxis.labels.dy = -6
    chart.categoryAxis.categoryNames = labels
    drawing.add(chart)
    if title:
        drawing.add(String(8, height - 9, title, fontSize=8, fillColor=DARK, fontName="Helvetica-Bold"))
    return drawing


def _kpi_table(rows: List[List[str]], widths: Optional[List[float]] = None) -> Table:
    table = Table(rows, colWidths=widths or [95, 95, 95, 95], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("TEXTCOLOR", (0, 0), (-1, 0), GREY),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8DEE9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


def _alerts_table(alerts: List[Alert], styles: Dict[str, ParagraphStyle]) -> Table:
    header = ["Horodatage", "Moteur", "Sévérité", "Type", "Message", "Statut"]
    data: List[List[Any]] = [[Paragraph(f"<b>{h}</b>", styles["small"]) for h in header]]
    for alert in alerts[:25]:
        stamp = datetime.fromtimestamp(alert.ts / 1000, tz=timezone.utc).strftime("%d/%m %H:%M:%S")
        data.append([
            Paragraph(stamp, styles["small"]),
            Paragraph(alert.motor_id, styles["small"]),
            Paragraph(f"<font color='{SEVERITY_COLORS.get(alert.severity, GREY).hexval()}'><b>{alert.severity}</b></font>",
                      styles["small"]),
            Paragraph(alert.type, styles["small"]),
            Paragraph(alert.message[:70], styles["small"]),
            Paragraph(alert.status, styles["small"]),
        ])
    table = Table(data, colWidths=[54, 34, 46, 54, 152, 46], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D8DEE9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def collect_report_data(motor_id: Optional[str] = None, hours: int = 24) -> Dict[str, Any]:
    """Rassemble toutes les données nécessaires au rapport."""
    since = int((time.time() - hours * 3600) * 1000)
    motors = [motor_id] if motor_id in ("M1", "M2") else ["M1", "M2"]
    with session_scope() as session:
        data: Dict[str, Any] = {"motors": {}, "generated_at": int(time.time() * 1000), "hours": hours}
        for mid in motors:
            snapshots = session.exec(
                select(HealthSnapshot).where(HealthSnapshot.motor_id == mid, HealthSnapshot.ts >= since)
                .order_by(HealthSnapshot.ts)
            ).all()
            alerts = session.exec(
                select(Alert).where(Alert.motor_id == mid, Alert.ts >= since).order_by(desc(Alert.ts))
            ).all()
            interventions = session.exec(
                select(Intervention).where(Intervention.motor_id == mid).order_by(desc(Intervention.ts))
            ).all()
            work_orders = session.exec(
                select(WorkOrder).where(WorkOrder.motor_id == mid).order_by(desc(WorkOrder.created_at))
            ).all()
            telemetry_last = hub.telemetry[mid].last() or {}
            data["motors"][mid] = {
                "health_series": [s.health_index for s in snapshots],
                "vibration_series": [s.vibration_rms for s in snapshots],
                "temp_series": [s.temp_bearing_c for s in snapshots],
                "current_series": [s.current_rms for s in snapshots],
                "avg_health": round(sum(s.health_index for s in snapshots) / len(snapshots), 1) if snapshots else None,
                "min_health": round(min((s.health_index for s in snapshots), default=0), 1) if snapshots else None,
                "samples": len(snapshots),
                "alerts": alerts,
                "interventions": interventions,
                "work_orders": work_orders,
                "last_telemetry": telemetry_last,
                "inference": hub.inference[mid].last(),
            }
        data["prescriptions"] = alert_engine.prescriptions()
        data["rates"] = hub.rates
        data["status"] = hub.status
        data["mqtt"] = {"mode": settings.mode, "enabled": settings.mqtt_enabled}
    return data


def build_pdf(motor_id: Optional[str] = None, hours: int = 24) -> bytes:
    """Construit le PDF et retourne son contenu binaire."""
    styles = _styles()
    data = collect_report_data(motor_id, hours)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Rapport de maintenance — Conveyor AI Ops",
        author=settings.report_author,
    )
    story: List[Any] = []
    generated = datetime.fromtimestamp(data["generated_at"] / 1000, tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    # ---------------- En-tête ----------------
    story.append(Paragraph("Rapport de supervision &amp; maintenance prédictive", styles["title"]))
    story.append(Paragraph(
        f"Convoyeur à bande bi-moteur (M1 GAUCHE / M2 DROIT) — {settings.report_company}<br/>"
        f"Généré le {generated} · fenêtre d'analyse : {hours} h · "
        f"modèle IA : {data.get('inference', {}).get('model_version', 'n/a') if data.get('inference') else 'n/a'}",
        styles["subtitle"]))
    story.append(_kpi_table([
        ["Indicateur", "Ligne", "M1 (gauche)", "M2 (droit)"],
        ["État", "En marche" if data["status"].get("running") else "Arrêt",
         (data["motors"].get("M1", {}).get("inference") or {}).get("fault_class", "—"),
         (data["motors"].get("M2", {}).get("inference") or {}).get("fault_class", "—")],
        ["Indice de santé moyen", "—",
         str(data["motors"].get("M1", {}).get("avg_health", "—")),
         str(data["motors"].get("M2", {}).get("avg_health", "—"))],
        ["RUL estimé", "—",
         f"{(data['motors'].get('M1', {}).get('inference') or {}).get('rul_hours', '—')} h",
         f"{(data['motors'].get('M2', {}).get('inference') or {}).get('rul_hours', '—')} h"],
        ["Vitesse cible", f"{data['status'].get('speed_rpm', '—')} rpm", "—", "—"],
        ["Débit télémétrie", f"{data['rates'].get('telemetry_per_s', 0)} msg/s", "—", "—"],
    ], widths=[105, 95, 95, 95]))

    # ---------------- Détail par moteur ----------------
    for mid, motor in data["motors"].items():
        inference = motor["inference"] or {}
        health = inference.get("health_index")
        story.append(Paragraph(f"Moteur {mid} — {'GAUCHE' if mid == 'M1' else 'DROIT'}", styles["h2"]))
        diag = (
            f"<b>Diagnostic IA :</b> {inference.get('fault_class', 'INCONNU')} · "
            f"indice de santé <b>{health if health is not None else '—'}</b>/100 · "
            f"score d'anomalie {inference.get('anomaly_score', '—')} · "
            f"RUL {inference.get('rul_hours', '—')} h "
            f"(intervalle {inference.get('rul_confidence', {}).get('low', '—')}–"
            f"{inference.get('rul_confidence', {}).get('high', '—')} h)"
        )
        story.append(Paragraph(diag, styles["body"]))
        if inference.get("explanation"):
            xai = " · ".join(f"{e.get('label', e.get('feature'))} {float(e.get('contribution', 0)) * 100:.0f} %"
                             for e in inference["explanation"][:4])
            story.append(Paragraph(f"<b>XAI :</b> {xai}", styles["small"]))
        story.append(Spacer(1, 5))
        story.append(_line_chart(motor["health_series"], title="Indice de santé (0–100)",
                                 color=_health_color(motor["avg_health"] or 100)))
        story.append(Spacer(1, 4))
        left = _bar_chart(motor["vibration_series"][-40:], ["" for _ in range(len(motor["vibration_series"][-40:]))],
                          width=230, height=110, color=CYAN, title="RMS vibratoire (g)")
        right = _bar_chart(motor["temp_series"][-40:], ["" for _ in range(len(motor["temp_series"][-40:]))],
                           width=230, height=110, color=AMBER, title="Température roulement (°C)",
                           max_value=100)
        story.append(Table([[left, right]], colWidths=[230, 230], hAlign="LEFT"))
        story.append(Spacer(1, 6))
        stats = [
            ["Échantillons (24 h)", "Alertes", "Alertes critiques", "Interventions"],
            [str(motor["samples"]), str(len(motor["alerts"])),
             str(len([a for a in motor["alerts"] if a.severity == "CRITICAL"])),
             str(len(motor["interventions"]))],
        ]
        story.append(_kpi_table(stats, widths=[105, 95, 95, 95]))
        if motor["alerts"]:
            story.append(Spacer(1, 8))
            story.append(_alerts_table(motor["alerts"], styles))
        story.append(PageBreak())

    # ---------------- Maintenance prescriptive ----------------
    story.append(Paragraph("Maintenance prescriptive", styles["h2"]))
    prescriptions = data["prescriptions"]
    if prescriptions:
        rows = [["Moteur", "Défaut", "Action recommandée", "Priorité", "Délai", "Durée", "Pièces"]]
        for p in prescriptions:
            rows.append([
                Paragraph(p["motor_id"], styles["cell"]),
                Paragraph(str(p["fault_class"]).replace("_", " "), styles["cell"]),
                Paragraph(p["action"], styles["cell"]),
                Paragraph(f"<font color='{SEVERITY_COLORS.get('CRITICAL' if p['priority'] == 'CRITICAL' else ('WARNING' if p['priority'] == 'HIGH' else 'INFO'), GREY).hexval()}'><b>{p['priority']}</b></font>", styles["cell"]),
                Paragraph(f"{p['due_in_days']} j", styles["cell"]),
                Paragraph(f"{p['estimated_hours']} h", styles["cell"]),
                Paragraph(", ".join(p["parts"]) or "—", styles["cell"]),
            ])
        table = Table(rows, colWidths=[36, 62, 150, 48, 32, 32, 92], repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D8DEE9")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("Aucune action corrective requise : les deux moteurs évoluent "
                               "dans leur signature nominale.", styles["body"]))

    # ---------------- Journal des interventions ----------------
    story.append(Paragraph("Journal des interventions (signé)", styles["h2"]))
    interventions = [i for motor in data["motors"].values() for i in motor["interventions"]]
    if interventions:
        rows = [["Date", "Moteur", "Technicien", "Action", "Durée", "Coût", "Signature"]]
        for item in interventions[:20]:
            stamp = datetime.fromtimestamp(item.ts / 1000, tz=timezone.utc).strftime("%d/%m/%Y %H:%M")
            rows.append([Paragraph(stamp, styles["cell"]), Paragraph(item.motor_id, styles["cell"]),
                         Paragraph(item.technician, styles["cell"]), Paragraph(item.action[:48], styles["cell"]),
                         Paragraph(f"{item.duration_min} min", styles["cell"]),
                         Paragraph(f"{item.cost_eur:.0f} €", styles["cell"]),
                         Paragraph(item.signature[:14], styles["small"])])
        table = Table(rows, colWidths=[62, 32, 62, 140, 40, 32, 84], repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D8DEE9")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("Aucune intervention enregistrée.", styles["body"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Rapport généré automatiquement par Conveyor AI Ops — pipeline IA "
        "(extraction de features → détection d'anomalie → classification → RUL → fusion Health Index). "
        "Les valeurs de RUL sont des estimations probabilistes à confronter à l'historique d'usure réel.",
        styles["small"]))

    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    logger.info("Rapport PDF généré (%s, %d h)", motor_id or "M1+M2", hours)
    return buffer.getvalue()


def _page_footer(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GREY)
    canvas.drawString(16 * mm, 9 * mm, "Conveyor AI Ops — Détection d'anomalies & maintenance prédictive")
    canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(colors.HexColor("#D8DEE9"))
    canvas.line(16 * mm, 12 * mm, A4[0] - 16 * mm, 12 * mm)
    canvas.restoreState()
