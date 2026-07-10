"""Vista PoC: carga viva de PDFs reales, sin persistencia.

La demo permite al cliente subir facturas/OC despues del meeting y ver como el
contrato de extraccion v2 clasifica y estructura cada documento. Los bytes se
procesan en memoria: no se guardan en disco, no se agregan al dataset sintetico
y no se envian a servicios externos.
"""

from __future__ import annotations

import csv
from io import StringIO

import pandas as pd
import streamlit as st

from ...extraction.pdf_poc import extract_document, read_pdf_bytes
from ...extraction.schema import FIELD_ORDER
from ..theme import badge


SUMMARY_FIELDS = [
    "document_type",
    "numero_factura",
    "po_reference",
    "proveedor_nombre_comercial",
    "importe_total",
    "moneda",
    "metodo_pago",
    "tratamiento_iva",
]


TYPE_LABELS = {
    "invoice": ("Factura fiscal", "info"),
    "proforma_or_advance_request": ("Proforma / anticipo", "flag"),
    "other": ("OC / otro documento", "mut"),
}


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ";".join(str(v) for v in value)
    return str(value)


def _results_csv(results) -> str:
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["archivo", "confidence", "pages", "text_chars", *FIELD_ORDER, "warnings"])
    for r in results:
        writer.writerow([
            r.doc_id,
            str(r.confidence),
            r.pages,
            r.text_chars,
            *[_csv_cell(r.document[f]) for f in FIELD_ORDER],
            " | ".join(r.warnings),
        ])
    return out.getvalue()


def _summary_rows(results) -> list[dict]:
    rows = []
    for r in results:
        doc = r.document
        rows.append({
            "archivo": r.doc_id,
            "tipo": doc["document_type"],
            "confianza": float(r.confidence),
            "numero": doc["numero_factura"],
            "po": doc["po_reference"],
            "proveedor": doc["proveedor_nombre_comercial"],
            "importe": doc["importe_total"],
            "moneda": doc["moneda"],
            "pago": doc["metodo_pago"],
            "iva": doc["tratamiento_iva"],
            "revision": " | ".join(r.warnings),
        })
    return rows


def _render_kpis(results) -> None:
    total = len(results)
    invoices = sum(1 for r in results if r.document["document_type"] == "invoice")
    advances = sum(1 for r in results if r.document["document_type"] == "proforma_or_advance_request")
    others = sum(1 for r in results if r.document["document_type"] == "other")
    review = sum(1 for r in results if r.warnings or r.confidence < 1)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Documentos", total)
    c2.metric("Facturas", invoices)
    c3.metric("Anticipos / OC", advances + others)
    c4.metric("A revisar", review)


def _render_detail(results) -> None:
    for r in results:
        doc = r.document
        label, kind = TYPE_LABELS[doc["document_type"]]
        with st.expander(r.doc_id, expanded=False):
            st.markdown(
                f"{badge(label, kind)} &nbsp; {badge('confianza ' + str(r.confidence), 'ok' if r.confidence >= 1 else 'flag')}"
                f"<div style='margin-top:8px;color:#5A6572;font-size:13px;'>"
                f"{r.pages} pagina(s) · {r.text_chars} caracteres extraidos"
                f"{('<br><b>Revision:</b> ' + ' | '.join(r.warnings)) if r.warnings else ''}"
                f"</div>",
                unsafe_allow_html=True,
            )
            rows = []
            for field in SUMMARY_FIELDS:
                rows.append({"campo": field, "valor": _csv_cell(doc[field])})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render() -> None:
    st.markdown("## PoC documentos reales")
    st.markdown(
        "<div class='apct-card'><b>Carga viva de facturas y órdenes de compra.</b><br>"
        "<span style='color:#5A6572;'>Los PDFs se procesan en memoria durante la sesión. "
        "No se guardan en disco, no modifican la corrida sintética y no salen de la app.</span></div>",
        unsafe_allow_html=True,
    )

    uploaded = st.file_uploader(
        "PDFs de factura / OC",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="visible",
    )
    if not uploaded:
        st.info("Cargá uno o más PDFs para ver la clasificación y los campos extraídos.")
        return

    results = []
    errors = []
    with st.spinner("Procesando PDFs..."):
        for file in uploaded:
            try:
                pdf = read_pdf_bytes(file.name, file.getvalue())
                results.append(extract_document(pdf))
            except Exception as exc:  # pragma: no cover - proteccion UI
                errors.append((file.name, str(exc)))

    if errors:
        for name, detail in errors:
            st.error(f"{name}: {detail}")
    if not results:
        return

    _render_kpis(results)
    st.dataframe(pd.DataFrame(_summary_rows(results)), use_container_width=True, hide_index=True)
    st.download_button(
        "Descargar extracción CSV",
        data=_results_csv(results).encode("utf-8-sig"),
        file_name="ap-control-tower-extraccion-pdf.csv",
        mime="text/csv",
        use_container_width=True,
    )
    _render_detail(results)
