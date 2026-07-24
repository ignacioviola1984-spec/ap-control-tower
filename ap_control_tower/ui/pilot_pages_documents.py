"""Ingreso y bandeja de documentos (lista → detalle)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import pandas as pd
import streamlit as st

from . import design, launcher
from .pilot_pages_common import (
    active_session_or_resume,
    render_document_detail,
    result_by_id,
    safe_document_rows,
)
from .trial import intake


def render_intake() -> None:
    intake.render()


#: Claves de los filtros. Se listan una vez para poder limpiarlos de golpe.
FILTER_KEYS = (
    "_docs_q", "_docs_estado", "_docs_prioridad", "_docs_mes", "_docs_prov",
    "_docs_moneda", "_docs_tipo", "_docs_venc_desde", "_docs_venc_hasta",
    "_docs_imp_min", "_docs_imp_max", "_docs_orden", "_docs_quick_view",
)

#: Prioridades en orden de consecuencia económica.
PRIORITIES = ["Crítica", "Alta", "Media", "Normal"]

#: Marca de forma (no de color) para que la prioridad se lea también en
#: escala de grises o con daltonismo.
PRIORITY_MARK = {"Crítica": "▲", "Alta": "●", "Media": "◆", "Normal": "·"}

#: Ayuda de estados: qué significa cada uno y qué se espera del operador.
STATE_HELP = (
    "Procesado: sin motivos pendientes. "
    "Requiere revisión: necesita una decisión humana. "
    "Retenido: frenado por decisión humana. "
    "Elegible: pasó los controles y espera aprobación de otra persona. "
    "Aprobado para propuesta: pasó el gate maker-checker. "
    "Rechazado / Excluido: fuera de la propuesta hasta nueva decisión."
)


def _filter_rows(rows: list[dict], *, query: str = "", states=(), suppliers=(),
                 currencies=(), types=(), month: str | None = None,
                 priorities=(), due_from=None, due_to=None,
                 amount_min=None, amount_max=None) -> list[dict]:
    """Aplica todos los filtros. Función pura: verificable sin interfaz."""
    needle = (query or "").strip().casefold()
    filtered = []
    for row in rows:
        haystack = " ".join(
            str(row[key]) for key in ("Documento", "Proveedor", "Número")
        ).casefold()
        if needle and needle not in haystack:
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
        if priorities and row["Prioridad"] not in priorities:
            continue
        if due_from is not None or due_to is not None:
            due = _row_due_date(row)
            if due is None:
                continue
            if due_from is not None and due < due_from:
                continue
            if due_to is not None and due > due_to:
                continue
        if amount_min is not None or amount_max is not None:
            amount = _row_amount(row)
            if amount is None:
                continue
            if amount_min is not None and amount < Decimal(str(amount_min)):
                continue
            if amount_max is not None and amount > Decimal(str(amount_max)):
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


def _row_issue_date(row: dict):
    text = str(row.get("_emision_raw") or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _clear_filters() -> None:
    for key in FILTER_KEYS:
        st.session_state.pop(key, None)


def _amount_column(rows: list[dict]):
    """Columna de importe numérica, alineada a la derecha.

    Con una sola moneda se usa su formato; con varias se muestra el número en
    formato contable y la moneda queda en su propia columna, porque un símbolo
    único sobre importes de distinta moneda sería sencillamente falso.
    """
    monedas = {row["Moneda"] for row in rows}
    presets = {"EUR": "euro", "USD": "dollar", "JPY": "yen"}
    formato = presets.get(next(iter(monedas))) if len(monedas) == 1 else None
    return st.column_config.NumberColumn(
        "Importe",
        format=formato or "accounting",
        alignment="right",
        help=("Importe total del documento."
              if formato else
              "Importe total. La sesión tiene varias monedas: la columna "
              "«Moneda» indica cuál corresponde a cada fila."),
    )


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
            "Los archivos con error se muestran en Ingreso de documentos.",
        )
        return

    # Un drill-down desde Inicio o desde el launcher llega con la vista y la
    # búsqueda ya elegidas. Se aplica ANTES de dibujar los widgets, que es la
    # única forma de fijar su valor inicial.
    preset = launcher.consume_preset()
    if preset:
        if preset.get("vista") in QUICK_VIEWS:
            st.session_state["_docs_quick_view"] = preset["vista"]
        if preset.get("buscar"):
            st.session_state["_docs_q"] = preset["buscar"]

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
    priority_options = [p for p in PRIORITIES if any(r["Prioridad"] == p for r in rows)]

    with st.expander("Filtros", icon=":material/filter_alt:"):
        with st.form("document_filters", border=False):
            first = st.columns([2, 1, 1, 1])
            query = first[0].text_input(
                "Buscar", placeholder="Proveedor, número o documento",
                icon=":material/search:", key="_docs_q",
            )
            states = first[1].multiselect(
                "Estado", state_options, placeholder="Todos", key="_docs_estado",
                help=STATE_HELP)
            priorities = first[2].multiselect(
                "Prioridad", priority_options, placeholder="Todas",
                key="_docs_prioridad",
                help="Derivada de los motivos de control, por consecuencia económica.")
            month = first[3].selectbox(
                "Mes de emisión", ["Todos", *month_options], key="_docs_mes",
                format_func=lambda value: (
                    value if value == "Todos" else f"{value[5:7]}/{value[:4]}"),
            )
            second = st.columns(4)
            suppliers = second[0].multiselect(
                "Proveedor", supplier_options, placeholder="Todos", key="_docs_prov")
            currencies = second[1].multiselect(
                "Moneda", currency_options, placeholder="Todas", key="_docs_moneda")
            types = second[2].multiselect(
                "Tipo documental", type_options, placeholder="Todos", key="_docs_tipo")
            order = second[3].selectbox(
                "Ordenar por",
                ["Fecha de emisión (más reciente)", "Vencimiento (más próximo)",
                 "Proveedor", "Estado", "Importe", "Prioridad"],
                key="_docs_orden",
            )
            third = st.columns(4)
            due_from = third[0].date_input(
                "Vence desde", value=None, format="DD/MM/YYYY",
                key="_docs_venc_desde")
            due_to = third[1].date_input(
                "Vence hasta", value=None, format="DD/MM/YYYY",
                key="_docs_venc_hasta")
            amount_min = third[2].number_input(
                "Importe mínimo", value=None, min_value=0.0, step=100.0,
                placeholder="Sin mínimo", key="_docs_imp_min")
            amount_max = third[3].number_input(
                "Importe máximo", value=None, min_value=0.0, step=100.0,
                placeholder="Sin máximo", key="_docs_imp_max")

            botones = st.columns([1, 1, 4])
            botones[0].form_submit_button(
                "Aplicar filtros", icon=":material/filter_alt:", width="stretch")
            limpiar = botones[1].form_submit_button(
                "Limpiar", icon=":material/filter_alt_off:", width="stretch")
    if limpiar:
        _clear_filters()
        st.rerun()

    filtered = _filter_rows(
        base_rows, query=query, states=states, suppliers=suppliers,
        currencies=currencies, types=types, month=month, priorities=priorities,
        due_from=due_from, due_to=due_to,
        amount_min=amount_min, amount_max=amount_max,
    )
    activos = sum(
        1 for item in (
            query, month if month != "Todos" else "", states, suppliers,
            currencies, types, priorities, due_from, due_to,
            amount_min, amount_max,
        ) if item
    )
    if activos or view != "Todos":
        st.caption(
            f"{len(filtered)} de {len(rows)} documento(s)"
            + (f" · vista «{view}»" if view != "Todos" else "")
            + (f" · {activos} filtro(s) activo(s)" if activos else "")
        )

    rank = {name: index for index, name in enumerate(PRIORITIES)}
    if order == "Proveedor":
        filtered.sort(key=lambda row: row["Proveedor"].casefold())
    elif order == "Estado":
        filtered.sort(key=lambda row: row["Estado"].casefold())
    elif order == "Importe":
        filtered.sort(key=lambda row: _row_amount(row) or Decimal("0"), reverse=True)
    elif order == "Prioridad":
        filtered.sort(key=lambda row: (rank.get(row["Prioridad"], 9),
                                       row["Proveedor"].casefold()))
    elif order == "Vencimiento (más próximo)":
        filtered.sort(key=lambda row: (_row_due_date(row) or date.max))
    else:
        filtered.sort(key=lambda row: row["month"], reverse=True)

    if not filtered:
        design.empty_state(
            "Ningún documento coincide",
            "Ajustá los filtros o volvé a la vista «Todos».",
        )
        if st.button("Limpiar filtros", icon=":material/filter_alt_off:",
                     key="_docs_clear_empty"):
            _clear_filters()
            st.rerun()
        return

    # El marco que viaja al navegador lleva SOLO columnas presentables: ningún
    # identificador fiscal, ninguna cuenta bancaria, ningún texto del PDF.
    frame = pd.DataFrame([
        {
            "Prioridad": f'{PRIORITY_MARK.get(row["Prioridad"], "")} {row["Prioridad"]}'.strip(),
            "Documento": row["Documento"],
            "Proveedor": row["Proveedor"],
            "Número": row["Número"],
            "Tipo": row["Tipo"],
            "Emisión": _row_issue_date(row),
            "Vencimiento": _row_due_date(row),
            "Moneda": row["Moneda"],
            "Importe": float(_row_amount(row)) if _row_amount(row) is not None else None,
            "Estado": row["Estado"],
        }
        for row in filtered
    ])
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
            "Prioridad": st.column_config.TextColumn(
                "Prioridad", pinned=True, width="small",
                help="▲ Crítica · ● Alta · ◆ Media · · Normal"),
            "Documento": st.column_config.TextColumn("Documento", pinned=True),
            "Proveedor": st.column_config.TextColumn("Proveedor", pinned=True),
            "Emisión": st.column_config.DateColumn("Emisión", format="DD/MM/YYYY"),
            "Vencimiento": st.column_config.DateColumn(
                "Vencimiento", format="DD/MM/YYYY",
                help="Vencimiento calculado o leído del documento."),
            "Moneda": st.column_config.TextColumn(
                "Moneda", width="small", help="Moneda del importe de la fila."),
            "Importe": _amount_column(filtered),
            "Estado": st.column_config.TextColumn("Estado", help=STATE_HELP),
        },
    )
    selected_rows = list(event.selection.rows)
    if not selected_rows:
        st.caption("Seleccioná un documento para ver el detalle.")
        return
    selected = filtered[selected_rows[0]]
    result = result_by_id(active, selected["doc_id"])

    # Acción sobre la fila seleccionada. Streamlit no permite incrustar botones
    # dentro de una celda, así que la acción vive junto a la tabla y opera
    # siempre sobre la selección visible.
    acciones = st.columns([1.2, 1.2, 3], gap="small")
    if acciones[0].button("Abrir en Revisión", icon=":material/fact_check:",
                          width="stretch", key="_docs_row_review"):
        st.switch_page("app_pages/revision_humana.py")
    if acciones[1].button("Ver en Pagos", icon=":material/payments:",
                          width="stretch", key="_docs_row_pay"):
        st.switch_page("app_pages/propuesta_pago.py")

    render_document_detail(active, result, agent_page_key="documentos")


__all__ = [
    "FILTER_KEYS", "PRIORITIES", "PRIORITY_MARK", "QUICK_VIEWS", "STATE_HELP",
    "apply_quick_view", "render_documents", "render_intake",
]
