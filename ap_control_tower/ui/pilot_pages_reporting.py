"""Auditoría e indicadores operativos del producto."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from .components import extraction_view as extraction
from .pilot_format import (
    format_date,
    format_datetime,
    label_for_code,
    operational_summary,
)
from .pilot_pages_common import metric_row, page_header
from .trial import session as sess


ROOT = Path(__file__).resolve().parents[2]


def render_audit() -> None:
    page_header(
        "Auditoría",
        "Eventos cronológicos de la sesión y verificación de integridad de la cadena.",
    )
    active = sess.get_session()
    events = active.audit.events
    integrity = active.audit.verify_chain()
    metric_row(
        [
            ("Eventos", len(events)),
            ("Integridad de la cadena", "Íntegra" if integrity else "Inconsistente"),
            (
                "Almacenamiento",
                "Historial disponible" if sess.persistence_available() else "Solo sesión",
            ),
        ]
    )
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
    frame = pd.DataFrame(
        [
            {
                "Secuencia": event.seq,
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


def render_indicators() -> None:
    page_header(
        "Indicadores",
        "Métricas operativas de la sesión y calidad de extracción medida.",
    )
    active = sess.get_session()
    operational = operational_summary(active)
    extraction_metrics = extraction.aggregate_metrics(active.results, active.errors)

    st.subheader("Operación de la sesión")
    metric_row(
        [
            ("Documentos recibidos", operational["received"]),
            ("Pendientes de revisión", operational["pending_review"]),
            ("Elegibles", operational["eligible"]),
            ("Aprobados para propuesta", operational["approved"]),
        ]
    )
    metric_row(
        [
            ("Campos encontrados", extraction_metrics["fields_found"]),
            (
                "Cobertura de extracción",
                f"{extraction_metrics['coverage'] * 100:.1f}%",
            ),
            (
                "Confianza informada",
                "—" if extraction_metrics["confidence"] is None
                else f"{extraction_metrics['confidence'] * 100:.1f}%",
            ),
            ("Errores", extraction_metrics["errors"]),
        ]
    )
    st.caption(
        "Cobertura y confianza describen la extracción; no equivalen a exactitud contable validada."
    )

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
    metric_row(
        [
            ("Documentos de referencia", golden.get("documentos", "—")),
            (
                "Exactitud de extracción",
                f"{latest.get('extraccion_exactitud_pct', 0):.1f}%" if latest else "—",
            ),
            (
                "Exactitud de ruteo",
                f"{replay.get('ruteo_exactitud_pct', latest.get('ruteo_exactitud_pct', 0)):.1f}%"
                if latest or replay else "—",
            ),
            ("Falsos negativos", replay.get("falsos_negativos", "—")),
        ]
    )
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
