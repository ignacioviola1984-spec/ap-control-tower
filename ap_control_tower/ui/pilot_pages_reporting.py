"""Auditoría e indicadores operativos del producto."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from . import design, indicators, launcher
from .extraction_metrics import aggregate_metrics
from .pilot_format import (
    format_date,
    format_datetime,
    label_for_code,
    operational_summary,
)
from .trial import session as sess


ROOT = Path(__file__).resolve().parents[2]


#: Acciones que produce el asistente. Es un dato estructurado del evento, no
#: una pista de texto: se compara contra la acción registrada, no contra su
#: descripción.
_AI_ACTIONS = {"consulta-asistente-ap"}

#: Claves de evidencia que nunca se muestran: podrían llevar contenido del
#: documento en vez de metadatos.
_EVIDENCE_BLOCKLIST = ("iban", "cuenta", "tax", "nif", "cif", "texto",
                       "contenido", "prompt", "respuesta")


def event_origin(event) -> str:
    """Origen del evento, derivado SOLO de campos estructurados.

    El texto del resultado no participa: antes un evento humano cuyo resultado
    contenía la palabra «error» se clasificaba como riesgo del sistema, que es
    una atribución equivocada y además contamina el filtro por origen.
    """
    if event.control_id:
        return "Control"
    if str(event.action or "") in _AI_ACTIONS:
        return "IA"
    if str(event.agent or "").casefold() in {"sistema", "system"}:
        return "Sistema"
    return "Humano"


def event_tone(event) -> str:
    """Severidad del evento, derivada del resultado registrado."""
    result_text = str(event.result or "").casefold()
    if any(token in result_text for token in ("error", "inconsist", "violation")):
        return "risk"
    if any(token in result_text for token in ("retenid", "excluid", "rechaz",
                                              "ambiguo", "no-encontrado")):
        return "warn"
    return {"Control": "info", "IA": "ai", "Sistema": "muted"}.get(
        event_origin(event), "ok")


def safe_evidence(evidence) -> str:
    """Resumen de la evidencia estructurada, sin datos del documento.

    La auditoría guarda metadatos (qué campos cambiaron, si hubo motivo), nunca
    valores. Aun así se filtra por clave y por longitud: si algún día entrara
    contenido, no saldría por esta pantalla.
    """
    if not isinstance(evidence, dict) or not evidence:
        return "—"
    partes = []
    for clave, valor in evidence.items():
        nombre = str(clave)
        if any(token in nombre.casefold() for token in _EVIDENCE_BLOCKLIST):
            continue
        if isinstance(valor, (list, tuple, set)):
            texto = ", ".join(str(item) for item in list(valor)[:6]) or "ninguno"
        elif isinstance(valor, bool):
            texto = "sí" if valor else "no"
        elif isinstance(valor, (int, float)):
            texto = str(valor)
        else:
            texto = str(valor)
            if len(texto) > 80:
                continue
        partes.append(f"{nombre.replace('_', ' ')}: {texto}")
    return " · ".join(partes[:6]) or "—"


def _event_origin(event) -> tuple[str, str]:
    """Compatibilidad: (etiqueta, tono) para el timeline."""
    return event_origin(event), event_tone(event)


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
                "Origen": event_origin(event),
                "Fecha y hora": format_datetime(event.ts),
                "Responsable": event.agent,
                "Acción": label_for_code(event.action),
                "Documento": event.invoice_id or "—",
                "Control": event.control_id or "—",
                "Resultado": label_for_code(event.result),
                "Evidencia": safe_evidence(getattr(event, "evidence", None)),
            }
            for event in filtered
        ]
    )
    if vista == "Tabla":
        tabla = st.dataframe(
            frame,
            hide_index=True,
            width="stretch",
            height=440,
            on_select="rerun",
            selection_mode="single-row",
            key="audit_table",
            column_config={
                "Secuencia": st.column_config.NumberColumn("Secuencia", format="%d", pinned=True),
                "Fecha y hora": st.column_config.TextColumn("Fecha y hora", pinned=True),
                "Origen": st.column_config.TextColumn(
                    "Origen",
                    help="Control · IA · Sistema · Humano. Se deriva del evento "
                         "registrado, no del texto del resultado."),
                "Control": st.column_config.TextColumn(
                    "Control", help="Identificador del control que produjo el evento."),
                "Evidencia": st.column_config.TextColumn(
                    "Evidencia",
                    help="Metadatos del evento. La auditoría no almacena valores "
                         "del documento, por eso no hay «antes y después» de cada "
                         "campo: sí queda registrado qué campos se corrigieron."),
            },
        )
        _render_event_detail(filtered, list(tabla.selection.rows))
    st.download_button(
        "Exportar auditoría CSV",
        data=frame.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"auditoria-{active.audit.run_id}.csv",
        mime="text/csv",
        icon=":material/download:",
        width="content",
    )


def _render_event_detail(events: list, selected_rows: list[int]) -> None:
    """Detalle del evento elegido, con salto al documento que lo originó."""
    if not selected_rows:
        st.caption("Seleccioná un evento para ver su detalle y abrir el documento.")
        return
    event = events[selected_rows[0]]
    with st.container(border=True):
        design.entity_header(
            f"Evento {event.seq}",
            label_for_code(event.action),
            chips=[design.chip(event_origin(event), event_tone(event))],
            meta=format_datetime(event.ts),
        )
        detalle = st.columns(2, gap="medium")
        detalle[0].write(f"**Responsable:** {event.agent}")
        detalle[0].write(f"**Resultado:** {label_for_code(event.result)}")
        detalle[1].write(f"**Control consultado:** {event.control_id or '—'}")
        detalle[1].write(f"**Documento:** {event.invoice_id or '—'}")
        st.write(f"**Evidencia:** {safe_evidence(getattr(event, 'evidence', None))}")
        if event.invoice_id:
            if st.button("Abrir el documento", icon=":material/description:",
                         key=f"_audit_open_{event.seq}"):
                st.session_state[launcher.PRESET_KEY] = {"buscar": str(event.invoice_id)}
                st.switch_page("app_pages/documentos.py")


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


def _render_operational_charts(active) -> None:
    """Gráficos nativos sobre datos de la sesión, cada uno con su drill-down."""
    from .pilot_format import STATE_LABELS

    estados, aging = st.columns(2, gap="medium")
    with estados.container(border=True):
        st.markdown("##### Documentos por estado")
        distribucion = indicators.state_distribution(active)
        st.bar_chart(
            pd.DataFrame(
                {"Documentos": list(distribucion.values())},
                index=[STATE_LABELS.get(code, code) for code in distribucion],
            ),
            horizontal=True, height=210, color="#0F4C81",
        )
        if st.button("Ver los que requieren revisión", icon=":material/rule:",
                     width="stretch", key="_ind_drill_state"):
            _drill({"vista": "Para revisar"})

    with aging.container(border=True):
        st.markdown("##### Antigüedad desde la emisión")
        st.caption("Días transcurridos desde la fecha de emisión del documento.")
        tramos = indicators.aging_distribution(active)
        st.bar_chart(
            pd.DataFrame({"Documentos": list(tramos.values())},
                         index=list(tramos)),
            height=210, color="#0F4C81",
        )

    vencimientos, exposicion = st.columns(2, gap="medium")
    with vencimientos.container(border=True):
        st.markdown("##### Próximos vencimientos")
        horizonte = indicators.due_distribution(active)
        st.bar_chart(
            pd.DataFrame({"Documentos": list(horizonte.values())},
                         index=list(horizonte)),
            height=210, color="#9A5B00",
        )
        if st.button("Ver los que vencen esta semana", icon=":material/event:",
                     width="stretch", key="_ind_drill_due"):
            _drill({"vista": "Vence esta semana"})

    with exposicion.container(border=True):
        st.markdown("##### Importe retenido por moneda")
        retenido = indicators.retained_amounts(active)
        if not retenido:
            st.caption("Ningún documento frenado en esta sesión.")
        else:
            st.dataframe(
                pd.DataFrame([
                    {"Moneda": currency, "Importe": float(value)}
                    for currency, value in sorted(retenido.items())
                ]),
                hide_index=True, width="stretch",
                column_config={
                    "Importe": st.column_config.NumberColumn(
                        "Importe", format="accounting", alignment="right"),
                },
            )
        st.caption(
            "No hay serie histórica en esta sesión, por eso ningún indicador "
            "muestra variación contra un período anterior."
        )


#: Período y fuente son iguales para todo lo que se calcula sobre la sesión.
_SESSION_SOURCE = "Período: sesión actual. Fuente: documentos y auditoría de la sesión."


def _drill(preset: dict) -> None:
    st.session_state[launcher.PRESET_KEY] = preset
    st.switch_page("app_pages/documentos.py")


def render_indicators() -> None:
    from .command_center import format_hours, median_cycle_hours

    design.page_header(
        "Indicadores",
        "Operación de la sesión y calidad de extracción, con la definición de "
        "cada métrica.",
    )
    active = sess.get_session()
    operational = operational_summary(active)
    extraction_metrics = aggregate_metrics(active.results, active.errors)

    st.subheader("Operación de la sesión")
    _kpi_grid([
        ("Documentos recibidos", operational["received"],
         "Definición: PDF ingresados en esta sesión. Unidad: recuento. "
         + _SESSION_SOURCE),
        ("Pendientes de revisión", operational["pending_review"],
         "Definición: documentos con una decisión humana pendiente. "
         "Unidad: recuento. " + _SESSION_SOURCE),
        ("Elegibles", operational["eligible"],
         "Definición: pasaron los controles y esperan aprobación separada. "
         "Unidad: recuento. " + _SESSION_SOURCE),
        ("Aprobados", operational["approved"],
         "Definición: superaron el gate maker-checker. Unidad: recuento. "
         + _SESSION_SOURCE),
    ])

    ciclo = median_cycle_hours(active)
    retenido = indicators.retained_amounts(active)
    _kpi_grid([
        ("Tiempo de ciclo (mediana)", format_hours(ciclo),
         "Definición: tiempo entre procesar un documento y su primera decisión "
         "humana. Unidad: horas o días. " + _SESSION_SOURCE
         + " ADVERTENCIA: se calcula sólo sobre documentos ya decididos; "
         "muestra «—» hasta que haya al menos uno."),
        ("Tasa touchless", indicators.percent(indicators.touchless_rate(active)),
         "Definición: documentos que llegaron a elegibles sin ninguna decisión "
         "humana. Unidad: porcentaje. " + _SESSION_SOURCE),
        ("Tasa de revisión humana",
         indicators.percent(indicators.human_review_rate(active)),
         "Definición: documentos derivados a revisión por al menos un control. "
         "Unidad: porcentaje. " + _SESSION_SOURCE),
        ("Excepciones autorizadas", indicators.exception_count(active),
         "Definición: documentos no fiscales habilitados por decisión humana "
         "explícita. Unidad: recuento. " + _SESSION_SOURCE),
    ])
    _kpi_grid([
        ("Importe retenido",
         design.money(*max(retenido.items(), key=lambda item: item[1])[::-1])
         if retenido else "—",
         "Definición: importe de los documentos frenados por revisión, "
         "retención, rechazo o exclusión. Unidad: moneda del documento. "
         + _SESSION_SOURCE
         + " ADVERTENCIA: si la sesión tiene varias monedas se muestra la de "
         "mayor importe; el detalle por moneda está en Inicio."),
        ("Cobertura de extracción", f"{extraction_metrics['coverage'] * 100:.1f}%",
         "Definición: campos con valor sobre el total del esquema. "
         "Unidad: porcentaje. " + _SESSION_SOURCE
         + " ADVERTENCIA: no equivale a exactitud contable."),
        ("Confianza informada",
         "—" if extraction_metrics["confidence"] is None
         else f"{extraction_metrics['confidence'] * 100:.1f}%",
         "Definición: confianza media que reporta el extractor por campo. "
         "Unidad: porcentaje. " + _SESSION_SOURCE
         + " ADVERTENCIA: no es una medida de exactitud verificada."),
        ("Errores de procesamiento", extraction_metrics["errors"],
         "Definición: archivos que no pudieron procesarse. Unidad: recuento. "
         + _SESSION_SOURCE),
    ])

    if operational["processed"]:
        _render_operational_charts(active)

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
