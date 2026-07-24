"""Ingreso y bandeja de documentos (lista → detalle)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import pandas as pd
import streamlit as st

from . import design
from .agent_panel import render_document_agent
from .pilot_pages_common import (
    active_session_or_resume,
    render_document_detail,
    result_by_id,
    safe_document_rows,
)
from .trial import intake


def render_intake() -> None:
    intake.render()


def _filter_rows(rows: list[dict], query: str, states: list[str],
                 suppliers: list[str], currencies: list[str],
                 types: list[str], month: str | None) -> list[dict]:
    query = (query or "").strip().casefold()
    filtered = []
    for row in rows:
        haystack = " ".join(
            str(row[key]) for key in ("Documento", "Proveedor", "Número")
        ).casefold()
        if query and query not in haystack:
            continue
        if states and row["Estado"] not in states:
            continue
        if suppliers and row["Proveedor"] not in suppliers:
            continue
        if currencies and row["Moneda"] not in currencies:
            continue
        if types and row["Tipo"] not in types:
            continue
        if month and month != "Todos" and row["month"] != month:
            continue
        filtered.append(row)
    return filtered


#: Vistas rápidas: el 90% de las consultas reales son una de estas cinco.
QUICK_VIEWS = ["Todos", "Para revisar", "Vence esta semana", "Alto importe",
               "Con anomalías"]


def apply_quick_view(rows: list[dict], view: str) -> list[dict]:
    """Filtro de la vista rápida. Función pura, verificable sin interfaz."""
    if view == "Para revisar":
        return [r for r in rows if r["state_code"] in {"pending_review", "retained"}]
    if view == "Con anomalías":
        return [r for r in rows if r["reasons"]]
    if view == "Vence esta semana":
        today = date.today()
        limit = today + timedelta(days=7)
        out = []
        for row in rows:
            due = _row_due_date(row)
            if due is not None and today <= due <= limit:
                out.append(row)
        return out
    if view == "Alto importe":
        amounts = [
            (_row_amount(row), row) for row in rows if _row_amount(row) is not None
        ]
        amounts.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in amounts[:max(5, len(amounts) // 4)]]
    return rows


def _row_amount(row: dict):
    try:
        return Decimal(str(row.get("_importe_raw")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _row_due_date(row: dict):
    text = str(row.get("_vencimiento_raw") or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def render_documents() -> None:
    design.page_header(
        "Documentos",
        "Bandeja de trabajo: filtrá, priorizá y abrí el detalle de cada documento.",
    )
    active = active_session_or_resume("documents")
    if active is None:
        return
    rows = safe_document_rows(active)
    if not rows:
        design.empty_state(
            "No hay documentos procesados",
            "Los archivos con error se muestran en la Bandeja de ingreso.",
        )
        return

    view = st.segmented_control(
        "Vista", QUICK_VIEWS, default="Todos", key="_docs_quick_view",
        label_visibility="collapsed",
    ) or "Todos"
    base_rows = apply_quick_view(rows, view)

    state_options = sorted({row["Estado"] for row in rows})
    supplier_options = sorted({row["Proveedor"] for row in rows})
    currency_options = sorted({row["Moneda"] for row in rows})
    type_options = sorted({row["Tipo"] for row in rows})
    month_options = sorted({row["month"] for row in rows if row["month"]})

    with st.expander("Filtros", icon=":material/filter_alt:"):
        with st.form("document_filters", border=False):
            first = st.columns([2, 1, 1])
            query = first[0].text_input(
                "Buscar", placeholder="Proveedor, número o documento",
                icon=":material/search:",
            )
            states = first[1].multiselect(
                "Estado", state_options, placeholder="Todos")
            month = first[2].selectbox(
                "Mes de emisión", ["Todos", *month_options],
                format_func=lambda value: (
                    value if value == "Todos" else f"{value[5:7]}/{value[:4]}"),
            )
            second = st.columns(4)
            suppliers = second[0].multiselect(
                "Proveedor", supplier_options, placeholder="Todos")
            currencies = second[1].multiselect(
                "Moneda", currency_options, placeholder="Todas")
            types = second[2].multiselect(
                "Tipo documental", type_options, placeholder="Todos")
            order = second[3].selectbox(
                "Ordenar por",
                ["Fecha de emisión (más reciente)", "Proveedor", "Estado", "Importe"],
            )
            st.form_submit_button(
                "Aplicar filtros", icon=":material/filter_alt:", width="content")

    filtered = _filter_rows(
        base_rows, query, states, suppliers, currencies, types, month
    )
    activos = sum(1 for item in (query, month if month != "Todos" else "",
                                 states, suppliers, currencies, types) if item)
    if activos or view != "Todos":
        st.caption(
            f"{len(filtered)} de {len(rows)} documento(s)"
            + (f" · vista «{view}»" if view != "Todos" else "")
            + (f" · {activos} filtro(s) activo(s)" if activos else "")
        )
    if order == "Proveedor":
        filtered.sort(key=lambda row: row["Proveedor"].casefold())
    elif order == "Estado":
        filtered.sort(key=lambda row: row["Estado"].casefold())
    elif order == "Importe":
        filtered.sort(key=lambda row: row["Importe"], reverse=True)
    else:
        filtered.sort(key=lambda row: row["month"], reverse=True)

    if not filtered:
        design.empty_state(
            "Ningún documento coincide",
            "Ajustá los filtros o volvé a la vista «Todos».",
        )
        return

    visible_columns = [
        "Prioridad", "Documento", "Proveedor", "Número", "Tipo", "Emisión",
        "Vencimiento", "Moneda", "Importe", "Estado",
    ]
    frame = pd.DataFrame(
        [{key: row.get(key, "") for key in visible_columns} for row in filtered]
    )
    event = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=360,
        on_select="rerun",
        selection_mode="single-row",
        selection_default={"selection": {"rows": [0]}},
        key="documents_list",
        column_config={
            "Documento": st.column_config.TextColumn("Documento", pinned=True),
            "Proveedor": st.column_config.TextColumn("Proveedor", pinned=True),
        },
    )
    selected_rows = list(event.selection.rows)
    if not selected_rows:
        st.caption("Seleccioná un documento para ver el detalle.")
        return
    selected = filtered[selected_rows[0]]
    result = result_by_id(active, selected["doc_id"])
    render_document_detail(active, result)
    render_document_agent(active, result, page_key="documentos")
