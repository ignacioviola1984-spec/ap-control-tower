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

from . import design
from .pilot_format import document_state, format_datetime, operational_summary
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
        "riesgos": risks,
        "duplicados": len(duplicates),
    }


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
    last_update = (
        active.audit.events[-1].ts if active.audit.events else active.created_at
    )

    head = st.container(horizontal=True, vertical_alignment="center")
    head.caption(f"Última actualización · {format_datetime(last_update)}")
    if head.button("Ingresar documentos", type="primary",
                   icon=":material/add:", key="_cc_intake"):
        st.switch_page("app_pages/ingreso_documentos.py")

    design.briefing(briefing_text(summary, signals))

    # Los KPI se muestran incluso con la sesión vacía: en ceros comunican qué
    # va a medir el circuito, y evitan que la portada arranque sin marco.
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
                f'<b>{item["proveedor"]}</b> · {design.money(item["importe"], item["moneda"])}'
                f'</span><div style="font-size:12.5px;color:#5A6B85;margin:2px 0 10px 0;">'
                f'{motivo}</div>'
            )
        if len(criticos) + len(altos) > 5:
            st.caption(f"y {len(criticos) + len(altos) - 5} más en Documentos.")

    st.markdown("##### Continuar el trabajo")
    acciones = st.columns(3, gap="medium")
    destinos = [
        ("Revisión", summary["pending_review"],
         "Documentos que necesitan una decisión humana.",
         "app_pages/revision_humana.py", ":material/fact_check:"),
        ("Pagos", summary["eligible"],
         "Elegibles que esperan aprobación separada.",
         "app_pages/propuesta_pago.py", ":material/payments:"),
        ("Documentos", summary["processed"],
         "Bandeja completa con filtros y detalle.",
         "app_pages/documentos.py", ":material/description:"),
    ]
    for column, (title, value, detail, page, icon) in zip(acciones, destinos):
        with column.container(border=True, height="stretch"):
            design.kpi(title, value, help_text=detail, border=False)
            if st.button("Abrir", icon=icon, key=f"_cc_go_{page}", width="stretch"):
                st.switch_page(page)
