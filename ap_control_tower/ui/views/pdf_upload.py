"""Vista PoC: carga viva de PDFs reales, sin persistencia.

La demo permite al cliente subir facturas/OC despues del meeting y ver como el
contrato de extraccion v2 clasifica y estructura cada documento. Las facturas
se procesan con Google Document AI dentro del proyecto cloud de la demo. La app
no conserva una copia local ni agrega los documentos al dataset sintetico.
"""

from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO

import pandas as pd
import streamlit as st

from ...extraction.document_ai import is_document_ai_configured
from ...extraction.gemma import extract_uploaded_document, is_gemma_configured
from ...extraction.schema import FIELD_ORDER
from ..theme import badge


DETAIL_FIELDS = [
    "document_type",
    "numero_factura",
    "fecha_emision",
    "po_reference",
    "proveedor_nombre_comercial",
    "proveedor_razon_social_legal",
    "proveedor_tax_id",
    "cliente_nombre",
    "cliente_tax_id",
    "importe_neto",
    "tipo_iva",
    "importe_iva",
    "importe_total",
    "moneda",
    "metodo_pago",
    "tratamiento_iva",
    "proveedor_banco",
    "proveedor_cuenta_bancaria",
    "iban",
    "bic",
    "condiciones_pago",
]


TYPE_LABELS = {
    "invoice": ("Factura fiscal", "info"),
    "proforma_or_advance_request": ("Proforma / anticipo", "flag"),
    "other": ("OC / otro documento", "mut"),
}

ENGINE_LABELS = {
    "gemma4_local_ollama": "Gemma 4 local",
    "google_document_ai_invoice_parser": "Document AI",
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
    writer.writerow(["archivo", "motor", "confidence", "pages", "text_chars", *FIELD_ORDER, "warnings"])
    for r in results:
        writer.writerow([
            r.doc_id,
            r.engine,
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
            "fecha": doc["fecha_emision"],
            "numero": doc["numero_factura"],
            "proveedor": doc["proveedor_nombre_comercial"],
            "cliente": doc["cliente_nombre"],
            "base": doc["importe_neto"],
            "IVA %": doc["tipo_iva"],
            "IVA importe": doc["importe_iva"],
            "total": doc["importe_total"],
            "moneda": doc["moneda"],
            "estado": "Revisar" if r.warnings else "OK",
        })
    return rows


def _render_kpis(results) -> None:
    total = len(results)
    invoices = sum(1 for r in results if r.document["document_type"] == "invoice")
    advances = sum(1 for r in results if r.document["document_type"] == "proforma_or_advance_request")
    others = sum(1 for r in results if r.document["document_type"] == "other")
    review = sum(1 for r in results if r.warnings)
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
            st.html(
                f"{badge(label, kind)} &nbsp; "
                f"{badge('confianza ' + str(r.confidence), 'ok' if not r.warnings else 'flag')} &nbsp; "
                f"{badge(ENGINE_LABELS.get(r.engine, 'motor local'), 'info' if r.engine in ENGINE_LABELS else 'mut')}"
                f"<div style='margin-top:8px;color:#5A6572;font-size:13px;'>"
                f"{r.pages} pagina(s) · {r.text_chars} caracteres extraidos"
                f"{('<br><b>Revision:</b> ' + ' | '.join(r.warnings)) if r.warnings else ''}"
                f"</div>",
            )
            rows = []
            for field in DETAIL_FIELDS:
                field_confidence = r.field_confidences.get(field)
                rows.append({
                    "campo": field,
                    "valor": _csv_cell(doc[field]),
                    "confianza": "" if field_confidence is None else str(field_confidence),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


@st.cache_data(show_spinner=False, ttl=1800)
def _process_batch_cached(files: tuple[tuple[str, bytes], ...]):
    results = [None] * len(files)
    errors: list[tuple[str, str]] = []
    workers = min(4, max(1, len(files)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(extract_uploaded_document, name, data): (index, name)
            for index, (name, data) in enumerate(files)
        }
        for future in as_completed(futures):
            index, name = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # pragma: no cover - proteccion de red/API
                errors.append((name, str(exc)))
    return [result for result in results if result is not None], errors


def render() -> None:
    st.markdown("## PoC documentos reales")
    st.html(
        "<div class='apct-card'><b>Carga viva de facturas y órdenes de compra.</b><br>"
        "<span style='color:#5A6572;'>Los PDFs se procesan en memoria durante la sesión con "
        "Gemma 4 corriendo en infraestructura propia (costo cero por documento). Document AI "
        "se usa solo como fallback pago para documentos que no validan limpio. "
        "La app no guarda una copia local ni modifica la corrida sintética.</span></div>",
    )

    if not is_gemma_configured():
        st.warning("Gemma 4 está desactivado (GEMMA_DISABLED). Se usará el flujo Document AI.")
    if not is_document_ai_configured():
        st.info("Document AI no está configurado: sin fallback pago, los documentos con avisos quedan a revisión.")

    uploaded = st.file_uploader(
        "PDFs de factura / OC",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="visible",
    )
    if not uploaded:
        st.info("Cargá uno o más PDFs para ver la clasificación y los campos extraídos.")
        return

    files = tuple((file.name, file.getvalue()) for file in uploaded)
    with st.spinner("Procesando PDFs con validación documental..."):
        results, errors = _process_batch_cached(files)

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
