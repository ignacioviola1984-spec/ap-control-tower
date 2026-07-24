"""Auditoría e indicadores operativos del producto."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from . import design
from .components import extraction_view as extraction
from .pilot_format import (
    format_date,
    format_datetime,
    label_for_code,
    operational_summary,
)
from .pilot_pages_common import page_header
from .trial import session as sess


ROOT = Path(__file__).resolve().parents[2]


def _event_origin(event) -> tuple[str, str]:
    """Clasifica el origen del evento para el timeline: (etiqueta, tono)."""
    result_text = str(event.result or "").casefold()
    if "inconsist" in result_text or "error" in result_text or "violation" in result_text:
        return "Riesgo", "risk"
    agent = str(event.agent or "").casefold()
    if event.control_id:
        return "Control", "info"
    if "asistente" in agent or "copiloto" in agent:
        return "IA", "ai"
    if agent in {"sistema", "system"}:
        return "Sistema", "muted"
    return "Humano", "ok"


def render_audit() -> None:
    design.page_header(
        "Auditoría",
        "Cadena de eventos de la sesión, por origen, con verificación de integridad.",
    )
    active = sess.get_session()
    events = active.audit.events
    integrity = active.audit.verify_chain()
    cols = st.columns(3, gap="small")
    with cols[0]:
        design.kpi("Eventos", len(events))
    with cols[1]:
        design.kpi("Integridad", "Íntegra" if integrity else "Inconsistente",
                   delta=None if integrity else "revisar",
                   delta_color="off" if integrity else "inverse")
    with cols[2]:
        design.kpi("Almacenamiento",
                   "Historial" if sess.persistence_available() else "Solo sesión")
    if integrity:
        st.success(
            "La cadena de auditoría conserva su integridad.",
            icon=":material/verified_user:",
        )
    else:
        st.error(
            "La cadena de auditoría es inconsistente. Detené las decisiones y contactá al administrador.",
            icon=":material/error:",
        )
    if not events:
        st.info("La sesión todavía no tiene eventos registrados.")
        return

    actions = sorted({event.action for event in events})
    actors = sorted({event.agent for event in events})
    documents = sorted({event.invoice_id for event in events if event.invoice_id})
    days = sorted({event.ts[:10] for event in events})
    with st.form("audit_filters", border=False):
        filters = st.columns(4)
        selected_actions = filters[0].multiselect(
            "Acción",
            actions,
            format_func=label_for_code,
            placeholder="Seleccionar acciones",
        )
        selected_actors = filters[1].multiselect(
            "Responsable", actors, placeholder="Seleccionar responsables"
        )
        selected_documents = filters[2].multiselect(
            "Documento", documents, placeholder="Seleccionar documentos"
        )
        selected_day = filters[3].selectbox(
            "Fecha",
            ["Todas", *days],
            format_func=lambda value: value if value == "Todas" else format_date(value),
        )
        st.form_submit_button(
            "Aplicar filtros",
            icon=":material/filter_alt:",
            width="content",
        )

    filtered = [
        event for event in events
        if (not selected_actions or event.action in selected_actions)
        and (not selected_actors or event.agent in selected_actors)
        and (not selected_documents or event.invoice_id in selected_documents)
        and (selected_day == "Todas" or event.ts.startswith(selected_day))
    ]
    if not filtered:
        st.info("No hay eventos que coincidan con los filtros seleccionados.")
        return

    vista = st.segmented_control(
        "Vista", ["Timeline", "Tabla"], default="Timeline",
        key="_audit_view", label_visibility="collapsed") or "Timeline"

    if vista == "Timeline":
        eventos = []
        for event in reversed(filtered[-60:]):
            origen, tono = _event_origin(event)
            detalle = label_for_code(event.action)
            if event.invoice_id:
                detalle += f" · {event.invoice_id}"
            eventos.append({
                "when": f"{format_datetime(event.ts)} · {origen}",
                "what": detalle,
                "who": f"{event.agent} → {label_for_code(event.result)}",
                "tone": tono,
            })
        design.timeline(eventos)

    frame = pd.DataFrame(
        [
            {
                "Secuencia": event.seq,
                "Origen": _event_origin(event)[0],
                "Fecha y hora": format_datetime(event.ts),
                "Responsable": event.agent,
                "Acción": label_for_code(event.action),
                "Documento": event.invoice_id or "—",
                "Control": event.control_id or "—",
                "Resultado": label_for_code(event.result),
            }
            for event in filtered
        ]
    )
    if vista == "Tabla":
        st.dataframe(
            frame,
            hide_index=True,
            width="stretch",
            height=440,
            column_config={
                "Secuencia": st.column_config.NumberColumn("Secuencia", format="%d", pinned=True),
                "Fecha y hora": st.column_config.TextColumn("Fecha y hora", pinned=True),
            },
        )
    st.download_button(
        "Exportar auditoría CSV",
        data=frame.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"auditoria-{active.audit.run_id}.csv",
        mime="text/csv",
        icon=":material/download:",
        width="content",
    )


@st.cache_data(ttl="15m", max_entries=4, show_spinner=False)
def _quality_summary() -> dict | None:
    path = ROOT / "evals" / "quality_summary.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _kpi_grid(items: list[tuple]) -> None:
    columns = st.columns(len(items), gap="small")
    for column, item in zip(columns, items):
        with column:
            design.kpi(item[0], item[1], help_text=item[2] if len(item) > 2 else "")


def render_indicators() -> None:
    design.page_header(
        "Indicadores",
        "Operación de la sesión y calidad de extracción, con la definición de "
        "cada métrica.",
    )
    active = sess.get_session()
    operational = operational_summary(active)
    extraction_metrics = extraction.aggregate_metrics(active.results, active.errors)

    st.subheader("Operación de la sesión")
    _kpi_grid([
        ("Documentos recibidos", operational["received"],
         "Definición: PDF ingresados en esta sesión. Fuente: sesión actual."),
        ("Pendientes de revisión", operational["pending_review"],
         "Definición: documentos con una decisión humana pendiente."),
        ("Elegibles", operational["eligible"],
         "Definición: pasaron los controles y esperan aprobación separada."),
        ("Aprobados", operational["approved"],
         "Definición: superaron el gate maker-checker."),
    ])
    _kpi_grid([
        ("Campos encontrados", extraction_metrics["fields_found"],
         "Definición: campos del esquema con valor. Unidad: recuento."),
        ("Cobertura de extracción", f"{extraction_metrics['coverage'] * 100:.1f}%",
         "Definición: campos con valor sobre el total del esquema. "
         "ADVERTENCIA: no equivale a exactitud contable."),
        ("Confianza informada",
         "—" if extraction_metrics["confidence"] is None
         else f"{extraction_metrics['confidence'] * 100:.1f}%",
         "Definición: confianza media que reporta el extractor. "
         "No es una medida de exactitud verificada."),
        ("Errores de procesamiento", extraction_metrics["errors"],
         "Definición: archivos que no pudieron procesarse."),
    ])

    st.subheader("Calidad de extracción")
    summary = _quality_summary()
    if not summary:
        st.info("No hay resultados de calidad versionados en esta instalación.")
        return
    golden = summary.get("golden_dataset", {})
    runs = summary.get("corridas", [])
    latest = runs[-1] if runs else {}
    replays = summary.get("policy_replays", [])
    replay = replays[-1] if replays else {}
    _kpi_grid([
        ("Documentos de referencia", golden.get("documentos", "—"),
         "Definición: facturas con verdad humana verificada. Fuente: golden versionado."),
        ("Exactitud de extracción",
         f"{latest.get('extraccion_exactitud_pct', 0):.1f}%" if latest else "—",
         "Definición: campos correctos sobre el golden. Período: última corrida."),
        ("Exactitud de ruteo",
         f"{replay.get('ruteo_exactitud_pct', latest.get('ruteo_exactitud_pct', 0)):.1f}%"
         if latest or replay else "—",
         "Definición: documentos ruteados al estado correcto."),
        ("Falsos negativos", replay.get("falsos_negativos", "—"),
         "Definición: riesgos que la política no marcó. Objetivo: cero."),
    ])
    st.caption(
        "Las métricas provienen del conjunto de referencia versionado y no constituyen una "
        "promesa de rendimiento sobre cualquier volumen futuro."
    )

    fields = summary.get("extraccion_por_campo_run2")
    if fields:
        field_frame = pd.DataFrame(fields)
        field_frame.columns = ["Campo", "Exactitud (%)"]
        st.dataframe(
            field_frame,
            hide_index=True,
            width="stretch",
            column_config={
                "Exactitud (%)": st.column_config.ProgressColumn(
                    "Exactitud (%)", min_value=0, max_value=100, format="%.1f%%"
                )
            },
        )
