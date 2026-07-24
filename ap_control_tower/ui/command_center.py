"""Inicio como centro de mando operativo.

El briefing se calcula con REGLAS deterministas sobre los datos ya extraídos,
no con una llamada al modelo: tiene que ser exacto y seguir funcionando con la
IA deshabilitada. La IA explica; los números los produce el sistema.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import streamlit as st

from . import design, launcher
from .pilot_format import (
    document_state,
    format_datetime,
    label_for_code,
    operational_summary,
)
from .trial import session as sess
from .trial import workflow


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _iso_date(value) -> date | None:
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def collect_signals(active, today: date | None = None) -> dict:
    """Señales operativas del lote. Función pura: se puede probar sin Streamlit."""
    today = today or date.today()
    horizon = today + timedelta(days=7)
    results = workflow.unique_results(active.results)
    duplicates = workflow.duplicate_doc_ids(results)

    due_soon: list[dict] = []
    overdue: list[dict] = []
    by_currency: dict[str, Decimal] = defaultdict(Decimal)
    retenido: dict[str, Decimal] = defaultdict(Decimal)
    risks: list[dict] = []
    attention = 0

    for result in results:
        document = result.document
        state, reasons = document_state(
            result, active.review_decisions, active.approval_decisions, duplicates
        )
        if state in {"pending_review", "retained"}:
            attention += 1

        amount = _decimal(document.get("importe_total"))
        currency = str(document.get("moneda") or "EUR").upper()
        if amount is not None and amount > 0:
            by_currency[currency] += amount
            # Importe retenido: lo que hoy NO puede pagarse porque una decisión
            # humana o un control lo frenó. Es la cifra que el responsable de AP
            # necesita para saber cuánto trabajo pendiente vale dinero.
            if state in {"retained", "pending_review", "rejected", "excluded"}:
                retenido[currency] += amount

        due = _iso_date(document.get("fecha_vencimiento_calculada"))
        entry = {
            "doc_id": result.doc_id,
            "proveedor": document.get("proveedor_nombre_comercial") or "—",
            "importe": amount,
            "moneda": currency,
            "vencimiento": due,
        }
        if due is not None and amount is not None:
            if due < today:
                overdue.append(entry)
            elif due <= horizon:
                due_soon.append(entry)

        label, tone = design.priority_tone(reasons)
        if tone in {"risk", "warn"}:
            risks.append({**entry, "prioridad": label, "tono": tone,
                          "motivos": reasons})

    def _total(entries: list[dict]) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = defaultdict(Decimal)
        for item in entries:
            if item["importe"] is not None:
                totals[item["moneda"]] += item["importe"]
        return dict(totals)

    return {
        "atencion": attention,
        "vence_pronto": due_soon,
        "vencidas": overdue,
        "total_vence_pronto": _total(due_soon),
        "total_vencidas": _total(overdue),
        "exposicion": dict(by_currency),
        "retenido": dict(retenido),
        "riesgos": risks,
        "duplicados": len(duplicates),
    }


#: Acciones humanas que cierran el ciclo de un documento.
_CLOSING_ACTIONS = {
    "revision-confirmada", "revision-retenida", "excepcion-pago-autorizada",
    "propuesta-pago-decidida",
}


def cycle_hours(active) -> list[float]:
    """Horas entre el procesamiento de cada documento y su primera decisión.

    Se calcula sobre eventos de auditoría reales. Un documento sin decisión no
    aporta: el tiempo de ciclo mide lo que se cerró, no lo que sigue abierto.
    """
    procesado: dict[str, datetime] = {}
    cerrado: dict[str, datetime] = {}
    for event in active.audit.events:
        doc_id = str(event.invoice_id or "")
        if not doc_id:
            continue
        try:
            when = datetime.fromisoformat(str(event.ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if event.action == "documento-procesado":
            procesado.setdefault(doc_id, when)
        elif event.action in _CLOSING_ACTIONS:
            cerrado.setdefault(doc_id, when)
    horas = []
    for doc_id, inicio in procesado.items():
        fin = cerrado.get(doc_id)
        if fin is None or fin < inicio:
            continue
        horas.append((fin - inicio).total_seconds() / 3600.0)
    return horas


def median_cycle_hours(active) -> float | None:
    """Mediana del tiempo de ciclo, o ``None`` si todavía no es calculable."""
    horas = sorted(cycle_hours(active))
    if not horas:
        return None
    medio = len(horas) // 2
    if len(horas) % 2:
        return horas[medio]
    return (horas[medio - 1] + horas[medio]) / 2


def format_hours(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 1:
        return f"{value * 60:.0f} min"
    if value < 48:
        return f"{value:.1f} h"
    return f"{value / 24:.1f} d"


def recent_activity(active, limit: int = 8) -> list[dict]:
    """Últimos eventos de auditoría, ya legibles. Función pura."""
    eventos = []
    for event in reversed(active.audit.events[-40:]):
        detalle = label_for_code(event.action)
        if event.invoice_id:
            detalle += f" · {event.invoice_id}"
        eventos.append({
            "when": format_datetime(event.ts),
            "what": detalle,
            "who": event.agent,
        })
        if len(eventos) >= limit:
            break
    return eventos


def briefing_text(summary: dict, signals: dict) -> str:
    """Frase de situación. Solo afirma lo que los datos sostienen."""
    if summary["received"] == 0:
        return "No hay documentos en esta sesión todavía."

    frases: list[str] = []
    if signals["atencion"]:
        frases.append(f"<b>{signals['atencion']} documento(s)</b> requieren tu atención")
    else:
        frases.append("Ningún documento requiere atención")

    montos = signals["total_vence_pronto"]
    if montos:
        detalle = " y ".join(
            f"<b>{design.money(value, currency)}</b>"
            for currency, value in sorted(montos.items())
        )
        frases.append(f"{detalle} vencen en los próximos 7 días")

    vencidas = signals["total_vencidas"]
    if vencidas:
        detalle = " y ".join(
            f"<b>{design.money(value, currency)}</b>"
            for currency, value in sorted(vencidas.items())
        )
        frases.append(f"{detalle} ya están vencidas")

    criticos = sum(1 for item in signals["riesgos"] if item["tono"] == "risk")
    if criticos:
        frases.append(f"se detectaron <b>{criticos} riesgo(s) de pago</b>")
    if signals["duplicados"]:
        frases.append(f"hay <b>{signals['duplicados']} posible(s) duplicado(s)</b>")

    return ". ".join(frases) + "."


def _kpi_row(items: list[tuple]) -> None:
    columns = st.columns(len(items), gap="small")
    for column, item in zip(columns, items):
        with column:
            design.kpi(item[0], item[1],
                       help_text=item[2] if len(item) > 2 else "")


def render_home() -> None:
    active = sess.get_session()
    summary = operational_summary(active)
    signals = collect_signals(active)

    design.page_header(
        "Inicio",
        "Situación del circuito de cuentas a pagar en esta sesión.",
    )
    # Acciones rápidas: entrar trabajo nuevo por los dos canales reales.
    # (La acción primaria "Ingresar documentos" vive en la barra superior.)
    rapidas = st.columns([1, 1, 3], gap="small")
    if rapidas[0].button("Consultar correo", icon=":material/mail:",
                         width="stretch", key="_cc_mail",
                         help="Buscar facturas nuevas en el buzón de AP"):
        st.switch_page("app_pages/ingreso_documentos.py")
    if rapidas[1].button("Subir PDF", icon=":material/upload_file:",
                         width="stretch", key="_cc_upload"):
        st.switch_page("app_pages/ingreso_documentos.py")

    design.briefing(briefing_text(summary, signals))

    # Los KPI se muestran incluso con la sesión vacía: en ceros comunican qué
    # va a medir el circuito, y evitan que la portada arranque sin marco.
    # NO llevan delta ni sparkline: esta sesión no tiene una serie histórica
    # contra la cual comparar, y un delta inventado es peor que ninguno.
    _kpi_row([
        ("Requieren atención", signals["atencion"],
         "Pendientes de revisión o retenidos"),
        ("Elegibles para pago", summary["eligible"],
         "Esperan un aprobador distinto del revisor"),
        ("Aprobados", summary["approved"], "Ya pasaron el gate maker-checker"),
        ("Errores", summary["errors"], "Archivos que no pudieron procesarse"),
    ])

    if summary["received"] == 0:
        design.empty_state(
            "Todavía no hay documentos",
            "Ingresá PDF o consultá la bandeja de correo para comenzar.",
        )
        return

    ciclo = median_cycle_hours(active)
    _kpi_row([
        ("Importe retenido",
         design.money(*_mayor_moneda(signals["retenido"]))
         if signals["retenido"] else "—",
         "Definición: importe de documentos frenados por revisión, retención, "
         "rechazo o exclusión. Fuente: documentos de la sesión."),
        ("Tiempo de ciclo (mediana)", format_hours(ciclo),
         "Definición: horas entre procesar un documento y su primera decisión "
         "humana. Fuente: auditoría. Se muestra «—» hasta que haya decisiones."),
        ("Exposición total",
         design.money(*_mayor_moneda(signals["exposicion"]))
         if signals["exposicion"] else "—",
         "Definición: suma de importes de los documentos de la sesión."),
    ])

    exposicion, riesgos = st.columns([1, 1], gap="medium")

    with exposicion.container(border=True, height="stretch"):
        st.markdown("##### Vencimientos y exposición")
        if signals["total_vencidas"]:
            for currency, value in sorted(signals["total_vencidas"].items()):
                design.alert(
                    f"{design.money(value, currency)} en {len(signals['vencidas'])} "
                    "documento(s) con vencimiento pasado.",
                    tone="risk", title="Vencido",
                )
        if signals["vence_pronto"]:
            for currency, value in sorted(signals["total_vence_pronto"].items()):
                design.alert(
                    f"{design.money(value, currency)} en {len(signals['vence_pronto'])} "
                    "documento(s) vencen dentro de 7 días.",
                    tone="warn", title="Próximos 7 días",
                )
        if not signals["vencidas"] and not signals["vence_pronto"]:
            st.caption("Sin vencimientos dentro de los próximos 7 días.")
        if signals["vencidas"] or signals["vence_pronto"]:
            if st.button("Ver estos vencimientos", icon=":material/event:",
                         width="stretch", key="_cc_drill_due"):
                _drill({"vista": "Vence esta semana"})
        if signals["exposicion"]:
            st.divider()
            st.caption("Exposición total de la sesión")
            for currency, value in sorted(signals["exposicion"].items()):
                st.markdown(f"**{design.money(value, currency)}**")

    with riesgos.container(border=True, height="stretch"):
        st.markdown("##### Riesgos y anomalías")
        criticos = [item for item in signals["riesgos"] if item["tono"] == "risk"]
        altos = [item for item in signals["riesgos"] if item["tono"] == "warn"]
        if not criticos and not altos:
            st.caption("Sin riesgos detectados en los documentos de la sesión.")
        for item in (criticos + altos)[:5]:
            motivo = item["motivos"][0] if item["motivos"] else "Revisar documento"
            st.html(
                design.chip(item["prioridad"], item["tono"])
                + f'<span style="margin-left:8px;font-size:13.5px;">'
                f'<b>{design.esc(item["proveedor"])}</b> · '
                f'{design.money(item["importe"], item["moneda"])}'
                f'</span><div style="font-size:12.5px;color:#5A6B85;margin:2px 0 10px 0;">'
                f'{design.esc(motivo)}</div>'
            )
        if criticos or altos:
            if len(criticos) + len(altos) > 5:
                st.caption(f"y {len(criticos) + len(altos) - 5} más.")
            if st.button("Ver todos los riesgos", icon=":material/warning:",
                         width="stretch", key="_cc_drill_risk"):
                _drill({"vista": "Con anomalías"})

    actividad, continuar = st.columns([1.15, 1], gap="medium")
    with actividad.container(border=True, height="stretch"):
        st.markdown("##### Actividad reciente")
        design.activity_panel(
            recent_activity(active),
            empty="Todavía no hay eventos en esta sesión.",
        )
        if st.button("Ver auditoría completa", icon=":material/history:",
                     width="stretch", key="_cc_go_audit"):
            st.switch_page("app_pages/auditoria.py")

    with continuar.container(border=True, height="stretch"):
        st.markdown("##### Continuar el trabajo")
        destinos = [
            ("Revisión", summary["pending_review"],
             "Necesitan una decisión humana.",
             "app_pages/revision_humana.py", ":material/fact_check:"),
            ("Pagos", summary["eligible"],
             "Esperan aprobación separada.",
             "app_pages/propuesta_pago.py", ":material/payments:"),
            ("Documentos", summary["processed"],
             "Bandeja completa con filtros.",
             "app_pages/documentos.py", ":material/description:"),
        ]
        for title, value, detail, page, icon in destinos:
            fila = st.columns([2, 1], gap="small", vertical_alignment="center")
            fila[0].html(
                f'<div style="font-size:14px;"><b>{design.esc(title)}</b> · '
                f'{design.esc(value)}</div>'
                f'<div style="font-size:12.5px;color:#5A6B85;">'
                f'{design.esc(detail)}</div>'
            )
            if fila[1].button("Abrir", icon=icon, key=f"_cc_go_{page}",
                              width="stretch"):
                st.switch_page(page)


def _mayor_moneda(totals: dict) -> tuple:
    """Moneda con mayor importe, para un KPI de una sola cifra."""
    currency, value = max(totals.items(), key=lambda item: item[1])
    return value, currency


def _drill(preset: dict) -> None:
    """Abre Documentos con un filtro ya aplicado."""
    st.session_state[launcher.PRESET_KEY] = preset
    st.switch_page("app_pages/documentos.py")
