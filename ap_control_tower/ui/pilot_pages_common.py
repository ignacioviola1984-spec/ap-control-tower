"""Componentes compartidos por las páginas operativas."""

from __future__ import annotations

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
        rows.append(
            {
                "doc_id": result.doc_id,
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
            }
        )
    return rows


def render_document_detail(active, result) -> None:
    document = result.document
    duplicates = workflow.duplicate_doc_ids(active.results)
    state, reasons = document_state(
        result, active.review_decisions, active.approval_decisions, duplicates
    )
    resolution = getattr(active, "supplier_resolutions", {}).get(str(result.doc_id))
    st.subheader(f"Detalle · {result.doc_id}")
    st.badge(
        STATE_LABELS[state],
        color=STATE_COLORS.get(state, "gray"),
        icon=":material/info:",
    )

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

    st.markdown("#### Motivos y advertencias")
    if reasons:
        for reason in reasons:
            st.warning(str(reason), icon=":material/warning:")
    reason_texts = {str(reason) for reason in reasons}
    informative = [
        str(warning) for warning in (result.warnings or [])
        if str(warning) not in reason_texts
    ]
    for warning in informative:
        st.info(warning, icon=":material/info:")
    if not reasons and not informative:
        st.success("No hay motivos de revisión pendientes.", icon=":material/check_circle:")

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

    st.markdown("#### Historial de decisiones")
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

    events = [event for event in active.audit.events if event.invoice_id == result.doc_id]
    st.markdown("#### Auditoría del documento")
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
