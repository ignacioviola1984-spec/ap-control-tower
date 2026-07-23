"""Inicio, ingreso y lista→detalle de documentos."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .agent_panel import render_document_agent
from .pilot_format import format_datetime, operational_summary
from .pilot_pages_common import (
    active_session_or_resume,
    metric_row,
    page_header,
    render_document_detail,
    result_by_id,
    safe_document_rows,
)
from .trial import intake
from .trial import session as sess


def render_home() -> None:
    page_header(
        "Inicio",
        "Resumen operativo de la sesión y tareas que requieren atención.",
    )
    active = sess.get_session()
    summary = operational_summary(active)
    last_update = active.audit.events[-1].ts if active.audit.events else active.created_at
    st.caption(f"Última actualización: {format_datetime(last_update)}")

    metric_row(
        [
            ("Documentos recibidos", summary["received"]),
            ("Procesados correctamente", summary["processed"]),
            ("Pendientes de revisión", summary["pending_review"]),
            ("Con advertencias", summary["warnings"]),
        ]
    )
    metric_row(
        [
            ("Elegibles para propuesta", summary["eligible"]),
            ("Aprobados para propuesta", summary["approved"]),
            ("Retenidos o excluidos", summary["retained_or_excluded"]),
            ("Errores de procesamiento", summary["errors"]),
        ]
    )

    if summary["received"] == 0:
        with st.container(border=True):
            st.subheader("No hay documentos en esta sesión")
            st.write(
                "Ingresá uno o varios PDF o consultá la bandeja de correo para comenzar."
            )
            if st.button(
                "Ir a Ingreso de documentos",
                icon=":material/upload_file:",
                key="home_open_intake",
            ):
                st.switch_page("app_pages/ingreso_documentos.py")
        return

    st.subheader("Atención requerida")
    task_cards = [
        (
            "Revisión humana",
            summary["pending_review"],
            "Documentos con campos, clasificación o controles que requieren decisión.",
            "app_pages/revision_humana.py",
            ":material/fact_check:",
        ),
        (
            "Propuesta de pago",
            summary["eligible"],
            "Documentos elegibles que esperan un aprobador distinto del revisor.",
            "app_pages/propuesta_pago.py",
            ":material/payments:",
        ),
        (
            "Errores recuperables",
            summary["errors"],
            "Archivos que pueden corregirse y volver a ingresarse.",
            "app_pages/ingreso_documentos.py",
            ":material/error:",
        ),
    ]
    columns = st.columns(3, gap="medium")
    for column, (title, value, description, page, icon) in zip(columns, task_cards):
        with column.container(border=True, height="stretch"):
            st.markdown(f"#### {title}")
            st.metric("Cantidad", value)
            st.caption(description)
            if st.button(
                "Abrir",
                icon=icon,
                key=f"home_open_{page}",
                width="stretch",
            ):
                st.switch_page(page)


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


def render_documents() -> None:
    page_header(
        "Documentos",
        "Buscá, filtrá y seleccioná un documento para consultar su detalle.",
    )
    active = active_session_or_resume("documents")
    if active is None:
        return
    rows = safe_document_rows(active)
    if not rows:
        st.info("No hay documentos procesados; los errores se muestran en Ingreso de documentos.")
        return

    state_options = sorted({row["Estado"] for row in rows})
    supplier_options = sorted({row["Proveedor"] for row in rows})
    currency_options = sorted({row["Moneda"] for row in rows})
    type_options = sorted({row["Tipo"] for row in rows})
    month_options = sorted({row["month"] for row in rows if row["month"]})

    with st.form("document_filters", border=False):
        first = st.columns([2, 1, 1])
        query = first[0].text_input(
            "Buscar",
            placeholder="Proveedor, número o documento",
            icon=":material/search:",
        )
        states = first[1].multiselect(
            "Estado", state_options, placeholder="Seleccionar estados"
        )
        month = first[2].selectbox(
            "Mes de emisión",
            ["Todos", *month_options],
            format_func=lambda value: (
                value if value == "Todos" else f"{value[5:7]}/{value[:4]}"
            ),
        )
        second = st.columns(4)
        suppliers = second[0].multiselect(
            "Proveedor", supplier_options, placeholder="Seleccionar proveedores"
        )
        currencies = second[1].multiselect(
            "Moneda", currency_options, placeholder="Seleccionar monedas"
        )
        types = second[2].multiselect(
            "Tipo documental", type_options, placeholder="Seleccionar tipos"
        )
        order = second[3].selectbox(
            "Ordenar por",
            ["Fecha de emisión (más reciente)", "Proveedor", "Estado", "Importe"],
        )
        st.form_submit_button(
            "Aplicar filtros",
            icon=":material/filter_alt:",
            width="content",
        )

    filtered = _filter_rows(
        rows, query, states, suppliers, currencies, types, month
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
        st.info("No hay documentos que coincidan con los filtros. Ajustá uno o más criterios.")
        return

    visible_columns = [
        "Documento", "Proveedor", "Número", "Tipo", "Emisión", "Vencimiento",
        "Moneda", "Importe", "Estado",
    ]
    frame = pd.DataFrame(
        [{key: row[key] for key in visible_columns} for row in filtered]
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
