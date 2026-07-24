"""Componentes compartidos por las páginas operativas."""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from ..persistence.masking import mask_account, mask_iban, mask_tax_id
from .pilot_format import (
    DOCUMENT_TYPE_LABELS,
    STATE_LABELS,
    document_state,
    format_amount,
    format_date,
    format_datetime,
    label_for_code,
    supplier_name,
)
from .trial import session as sess
from .trial import workflow


STATE_COLORS = {
    "processed": "green",
    "pending_review": "orange",
    "retained": "orange",
    "eligible": "blue",
    "approved": "green",
    "rejected": "red",
    "excluded": "gray",
    "error": "red",
}


def page_header(title: str, description: str) -> None:
    st.title(title)
    st.caption(description)


def metric_row(metrics: list[tuple[str, object]]) -> None:
    """Distribuye métricas en una grilla estable y adaptable al ancho disponible."""
    columns = st.columns(len(metrics), gap="medium")
    for column, (label, value) in zip(columns, metrics):
        column.metric(label, value, border=True)


def active_session_or_resume(key_prefix: str):
    active = sess.get_session()
    if active.results or active.errors:
        return active

    st.info(
        "Todavía no hay documentos en la sesión actual. Ingresá documentos o "
        "continuá un proceso guardado."
    )
    if not sess.persistence_available():
        if st.button(
            "Ir a Ingreso de documentos",
            icon=":material/upload_file:",
            key=f"_{key_prefix}_open_intake",
        ):
            st.switch_page("app_pages/ingreso_documentos.py")
        return None
    try:
        runs = sess.saved_runs()
    except Exception:
        st.error(
            "No fue posible consultar el historial. La carga manual sigue disponible."
        )
        return None
    if not runs:
        st.caption("No hay procesos guardados para reanudar.")
        return None
    labels = {
        f"{run['created_at'].strftime('%d/%m/%Y %H:%M')} · "
        f"{run['documents']} documento(s)": run["run_id"]
        for run in runs
    }
    selected = st.selectbox(
        "Proceso guardado",
        list(labels),
        key=f"_{key_prefix}_resume_select",
    )
    if st.button(
        "Continuar proceso",
        icon=":material/restore:",
        key=f"_{key_prefix}_resume_button",
    ):
        try:
            sess.resume_saved_run(labels[selected])
        except Exception:
            st.error("El proceso seleccionado ya no está disponible. Actualizá la lista.")
        else:
            st.rerun()
    return None


def result_by_id(active, doc_id: str):
    for result in workflow.unique_results(active.results):
        if str(result.doc_id) == str(doc_id):
            return result
    raise ValueError("El documento seleccionado ya no está disponible.")


def _sage_resolution_label(resolution: dict | None) -> str:
    if not resolution:
        return "Sin maestro aplicado"
    method = resolution.get("method")
    if resolution.get("status") == "matched" and method == "tax_id":
        return "Vinculado por Tax ID"
    if resolution.get("status") == "matched" and method == "exact_name":
        return "Vinculado por nombre normalizado"
    if resolution.get("status") == "matched" and method == "fuzzy_name":
        score = resolution.get("score")
        return (
            f"Vinculado por similitud de nombre ({float(score):.0%})"
            if score is not None else "Vinculado por similitud de nombre"
        )
    if resolution.get("status") == "ambiguous":
        return f"Ambiguo · {resolution.get('candidate_count', 0)} candidatos"
    return "Proveedor no encontrado"


def safe_document_rows(active) -> list[dict]:
    results = workflow.unique_results(active.results)
    duplicates = workflow.duplicate_doc_ids(results)
    rows = []
    for result in results:
        document = result.document
        state, reasons = document_state(
            result, active.review_decisions, active.approval_decisions, duplicates
        )
        from . import design

        priority, _tone = design.priority_tone(reasons)
        rows.append(
            {
                "doc_id": result.doc_id,
                "Prioridad": priority,
                "Documento": result.doc_id,
                "Proveedor": supplier_name(document),
                "Número": str(document.get("numero_factura") or "—"),
                "Tipo": DOCUMENT_TYPE_LABELS.get(
                    document.get("document_type"), "Sin clasificar"
                ),
                "Emisión": format_date(document.get("fecha_emision")),
                "Vencimiento": format_date(
                    document.get("fecha_vencimiento_calculada")
                    or document.get("fecha_vencimiento_texto")
                ),
                "Moneda": str(document.get("moneda") or "—").upper(),
                "Importe": format_amount(
                    document.get("importe_total"), document.get("moneda")
                ),
                "Estado": STATE_LABELS[state],
                "state_code": state,
                "reasons": reasons,
                "month": str(document.get("fecha_emision") or "")[:7],
                # Valores crudos para ordenar y filtrar sin reparsear el texto
                # ya formateado (que lleva separadores de miles y moneda).
                "_emision_raw": document.get("fecha_emision"),
                "_importe_raw": document.get("importe_total"),
                "_vencimiento_raw": (
                    document.get("fecha_vencimiento_calculada")
                    or document.get("fecha_vencimiento_texto")
                ),
            }
        )
    return rows


def _pdf_bytes_for(doc_id) -> bytes | None:
    blobs = st.session_state.get("_ap_pdf_blobs") or {}
    return blobs.get(str(doc_id))


def _render_pdf_inline(data: bytes) -> None:
    """Incrusta el PDF, o explica por qué no se puede, sin romper la página.

    st.pdf existe desde Streamlit 1.57 y delega en el componente opcional
    streamlit-pdf. Si falta cualquiera de los dos, la llamada levanta y se
    llevaba por delante toda la vista de detalle: el revisor perdía los datos
    extraídos, los controles y el historial, no sólo el visor.
    """
    try:
        st.pdf(io.BytesIO(data), height=640)
    except Exception:  # noqa: BLE001 - el visor nunca debe tumbar la revisión
        st.info(
            "El visor incrustado no está disponible en este entorno. "
            "Descargá el PDF para revisarlo.",
            icon=":material/info:",
        )


def render_pdf_viewer(result) -> None:
    """Muestra el PDF original al revisor humano (bytes solo en memoria de sesión)."""
    data = _pdf_bytes_for(result.doc_id)
    if not data:
        return
    with st.expander("Ver PDF original", icon=":material/picture_as_pdf:"):
        _render_pdf_inline(data)
        st.download_button(
            "Descargar PDF",
            data=data,
            file_name=f"{result.doc_id}.pdf",
            mime="application/pdf",
            icon=":material/download:",
            key=f"_pdf_dl_{result.doc_id}",
        )
        st.caption(
            "El PDF se muestra al revisor y se conserva solo en memoria de esta "
            "sesión; no se envía a OpenAI ni se almacena."
        )


def render_document_detail(active, result, *, agent_page_key: str | None = None) -> None:
    """Detalle del documento en pestañas.

    Antes era una sola columna con siete bloques apilados: el revisor tenía que
    recorrer toda la página para llegar a la auditoría. Las pestañas mantienen
    el mismo contenido pero dejan cada cosa a un clic, sin scroll largo.
    """
    from . import design

    document = result.document
    duplicates = workflow.duplicate_doc_ids(active.results)
    state, reasons = document_state(
        result, active.review_decisions, active.approval_decisions, duplicates
    )
    resolution = getattr(active, "supplier_resolutions", {}).get(str(result.doc_id))
    prioridad, tono = design.priority_tone(reasons)
    design.entity_header(
        str(result.doc_id),
        supplier_name(document),
        chips=[
            design.chip(STATE_LABELS[state], design.state_tone(state)),
            design.chip(prioridad, tono),
        ],
        meta=format_amount(document.get("importe_total"), document.get("moneda")),
    )

    nombres = ["Resumen", "PDF", "Datos", "Controles", "Historial", "Auditoría"]
    if agent_page_key:
        nombres.append("Copiloto")
    pestanas = st.tabs(nombres)
    with pestanas[0]:
        _detail_summary(result, reasons, resolution)
    with pestanas[1]:
        _detail_pdf(result)
    with pestanas[2]:
        _detail_fields(document, resolution)
    with pestanas[3]:
        _detail_controls(result, document, duplicates, resolution)
    with pestanas[4]:
        _detail_history(active, result)
    with pestanas[5]:
        _detail_audit(active, result)
    if agent_page_key:
        with pestanas[6]:
            from .agent_panel import render_document_agent

            render_document_agent(active, result, page_key=agent_page_key)


def _detail_summary(result, reasons, resolution) -> None:
    from . import design

    if reasons:
        for reason in reasons:
            _label, tone = design.priority_tone([reason])
            design.alert(str(reason),
                         tone=tone if tone != "muted" else "info",
                         title="Motivo de revisión")
    reason_texts = {str(reason) for reason in reasons}
    informative = [
        str(warning) for warning in (result.warnings or [])
        if str(warning) not in reason_texts
    ]
    for warning in informative:
        st.info(warning, icon=":material/info:")
    if not reasons and not informative:
        st.success("No hay motivos de revisión pendientes.",
                   icon=":material/check_circle:")
    st.caption(
        "Vinculación con el maestro de Sage · "
        + _sage_resolution_label(resolution)
    )


def _detail_pdf(result) -> None:
    data = _pdf_bytes_for(result.doc_id)
    if not data:
        from . import design

        design.empty_state(
            "El PDF original no está en memoria",
            "Se conserva sólo durante la sesión en la que se ingresó el documento.",
        )
        return
    _render_pdf_inline(data)
    st.download_button(
        "Descargar PDF",
        data=data,
        file_name=f"{result.doc_id}.pdf",
        mime="application/pdf",
        icon=":material/download:",
        key=f"_pdf_dl_tab_{result.doc_id}",
    )
    st.caption(
        "El PDF se muestra al revisor y se conserva solo en memoria de esta "
        "sesión; no se envía a OpenAI ni se almacena."
    )


def _detail_fields(document: dict, resolution) -> None:
    identity, finance = st.columns(2, gap="medium")
    with identity.container(border=True, height="stretch"):
        st.markdown("#### Identificación")
        st.write(f"**Proveedor:** {supplier_name(document)}")
        st.write(f"**Vinculación Sage:** {_sage_resolution_label(resolution)}")
        st.write(f"**Número:** {document.get('numero_factura') or '—'}")
        st.write(
            "**Tipo:** "
            + DOCUMENT_TYPE_LABELS.get(document.get("document_type"), "Sin clasificar")
        )
        st.write(f"**Fecha de emisión:** {format_date(document.get('fecha_emision'))}")
        st.write(
            "**Referencia de OC:** " + str(document.get("po_reference") or "—")
        )
    with finance.container(border=True, height="stretch"):
        st.markdown("#### Importe y vencimiento")
        st.write(
            "**Vencimiento:** "
            + format_date(
                document.get("fecha_vencimiento_calculada")
                or document.get("fecha_vencimiento_texto")
            )
        )
        st.write(
            "**Importe total:** "
            + format_amount(document.get("importe_total"), document.get("moneda"))
        )
        if document.get("retencion_irpf") not in (None, ""):
            st.write(
                "**Retención IRPF:** −"
                + format_amount(document.get("retencion_irpf"), document.get("moneda"))
            )
        if document.get("saldo_pendiente") not in (None, ""):
            saldo = format_amount(document.get("saldo_pendiente"), document.get("moneda"))
            try:
                pagada = float(str(document["saldo_pendiente"])) == 0
            except (TypeError, ValueError):
                pagada = False
            st.write(
                f"**Saldo pendiente:** {saldo}"
                + ("  ·  :red[**ya pagada**]" if pagada else "")
            )
        st.write(f"**Moneda:** {str(document.get('moneda') or '—').upper()}")
        st.write(f"**CUIT / ID fiscal:** {mask_tax_id(document.get('proveedor_tax_id')) or '—'}")
        st.write(
            "**Cuenta bancaria:** "
            + str(
                mask_iban(document.get("iban"))
                or mask_account(document.get("proveedor_cuenta_bancaria"))
                or "—"
            )
        )
    st.caption(
        "Los identificadores fiscales y bancarios se muestran enmascarados: "
        "alcanzan para verificar, no para copiar."
    )


def _detail_controls(result, document: dict, duplicates, resolution) -> None:
    missing = workflow.missing_critical_fields(document)
    arca_signals = [
        item for item in result.warnings
        if "arca" in str(item).casefold() or "apócrif" in str(item).casefold()
    ]
    controls = pd.DataFrame(
        [
            {
                "Control": "Extracción documental",
                "Resultado": (
                    "Google Document AI"
                    if result.engine == "google_document_ai_invoice_parser"
                    else "Motor local controlado"
                ),
            },
            {
                "Control": "Campos críticos",
                "Resultado": "Completos" if not missing else "Faltan: " + ", ".join(missing),
            },
            {
                "Control": "Duplicados comerciales",
                "Resultado": "Posible duplicado" if result.doc_id in duplicates else "Sin coincidencias",
            },
            {
                "Control": "Maestro de proveedores Sage",
                "Resultado": _sage_resolution_label(resolution),
            },
            {
                "Control": "ARCA",
                "Resultado": " · ".join(str(item) for item in arca_signals)
                if arca_signals
                else "Sin señal registrada",
            },
        ]
    )
    st.dataframe(controls, hide_index=True, width="stretch")


def _detail_history(active, result) -> None:
    history = []
    review = active.review_decisions.get(result.doc_id)
    payment = active.approval_decisions.get(result.doc_id)
    if review:
        history.append(
            {
                "Fecha y hora": format_datetime(review.get("timestamp")),
                "Responsable": review.get("actor") or "—",
                "Decisión": label_for_code(review.get("status")),
                "Nota": review.get("note") or "—",
            }
        )
    if payment:
        history.append(
            {
                "Fecha y hora": format_datetime(payment.get("timestamp")),
                "Responsable": payment.get("actor") or "—",
                "Decisión": label_for_code(payment.get("status")),
                "Nota": payment.get("note") or "—",
            }
        )
    if history:
        st.dataframe(pd.DataFrame(history), hide_index=True, width="stretch")
    else:
        st.caption("Todavía no hay decisiones humanas registradas para este documento.")


def _detail_audit(active, result) -> None:
    events = [event for event in active.audit.events if event.invoice_id == result.doc_id]
    if events:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Fecha y hora": format_datetime(event.ts),
                        "Responsable": event.agent,
                        "Acción": label_for_code(event.action),
                        "Resultado": label_for_code(event.result),
                    }
                    for event in events
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("No hay eventos específicos para este documento.")
