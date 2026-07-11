"""Vista reutilizable de resultados de extraccion (demo Gmail + app trial).

Reune los helpers de presentacion que antes vivian en la vista PoC: tabla
resumen, CSV, detalle por documento y metricas. Regla de honestidad: NO se
afirma "precision" sin validacion humana; se muestran cobertura de extraccion,
confianza, campos encontrados/ausentes y advertencias.

Sin estado propio: recibe una lista de PocResult y renderiza. No cachea ni
guarda contenido; los datos viven en la sesion de quien lo llama.
"""

from __future__ import annotations

import csv
from io import StringIO

import pandas as pd
import streamlit as st

from ...extraction.schema import FIELD_ORDER
from ..theme import badge

# Campos de negocio para cobertura (se excluyen banderas internas).
_NON_BUSINESS = {"iban_enmascarado", "campos_ilegibles"}
BUSINESS_FIELDS = [f for f in FIELD_ORDER if f not in _NON_BUSINESS]

DETAIL_FIELDS = [
    "document_type", "numero_factura", "fecha_emision",
    "fecha_vencimiento_texto", "fecha_vencimiento_calculada",
    "po_reference", "project_reference",
    "proveedor_nombre_comercial", "proveedor_razon_social_legal", "proveedor_tax_id",
    "cliente_nombre", "cliente_tax_id",
    "importe_neto", "tipo_iva", "importe_iva", "importe_total", "moneda",
    "metodo_pago", "tratamiento_iva",
    "proveedor_banco", "proveedor_cuenta_bancaria", "iban", "bic", "condiciones_pago",
]

TYPE_LABELS = {
    "invoice": ("Factura fiscal", "info"),
    "proforma_or_advance_request": ("Proforma / anticipo", "flag"),
    "other": ("OC / otro documento", "mut"),
}


def process_files(files, on_progress=None):
    """Procesa PDFs INLINE (sin cache): [(nombre, bytes)] -> (results, errores).

    Reutiliza el unico adaptador de Document AI de la demo. No guarda los bytes:
    se procesan y se descartan; solo se devuelve el resultado estructurado.
    ``on_progress(i, total, nombre)`` es opcional para feedback de UI.
    """
    from ...app import process_uploaded_document

    results, errors = [], []
    total = len(files)
    for index, (name, data) in enumerate(files, 1):
        try:
            results.append(process_uploaded_document(name, data))
        except Exception as exc:  # proteccion de red/API: mensaje claro, no crash
            errors.append((name, str(exc)))
        if on_progress is not None:
            on_progress(index, total, name)
    return results, errors


def _is_present(value) -> bool:
    return value not in (None, "", [], {})


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ";".join(str(v) for v in value)
    return str(value)


def _vencimiento(doc: dict) -> str:
    return _cell(doc.get("fecha_vencimiento_calculada") or doc.get("fecha_vencimiento_texto"))


def _referencias(doc: dict) -> str:
    refs = [doc.get("po_reference"), doc.get("project_reference")]
    return " · ".join(str(r) for r in refs if r) or ""


def present_fields(result) -> list[str]:
    return [f for f in BUSINESS_FIELDS if _is_present(result.document.get(f))]


def missing_fields(result) -> list[str]:
    return [f for f in BUSINESS_FIELDS if not _is_present(result.document.get(f))]


def coverage(result) -> float:
    return len(present_fields(result)) / len(BUSINESS_FIELDS) if BUSINESS_FIELDS else 0.0


def results_csv(results) -> str:
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["archivo", "motor", "confidence", "pages", "text_chars",
                     *FIELD_ORDER, "warnings"])
    for r in results:
        writer.writerow([
            r.doc_id, r.engine, str(r.confidence), r.pages, r.text_chars,
            *[_cell(r.document.get(f)) for f in FIELD_ORDER],
            " | ".join(r.warnings),
        ])
    return out.getvalue()


# ------------------------------------------------------------------ render
def render_metrics(results) -> None:
    total = len(results)
    cov = sum(coverage(r) for r in results) / total if total else 0.0
    conf = sum(float(r.confidence) for r in results) / total if total else 0.0
    with_warn = sum(1 for r in results if r.warnings)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Documentos procesados", total)
    c2.metric("Cobertura de extracción", f"{cov*100:.0f}%", help="Campos encontrados sobre el total del esquema (no es una medida de exactitud).")
    c3.metric("Confianza media", f"{conf*100:.0f}%", help="Confianza del extractor. No implica exactitud verificada.")
    c4.metric("Documentos con advertencias", with_warn)
    st.caption("No se afirma exactitud sin validación humana: estas métricas son "
               "cobertura, confianza y advertencias del extractor.")


def _summary_rows(results) -> list[dict]:
    rows = []
    for r in results:
        doc = r.document
        rows.append({
            "archivo": r.doc_id,
            "tipo": doc.get("document_type"),
            "proveedor": doc.get("proveedor_nombre_comercial"),
            "cliente": doc.get("cliente_nombre"),
            "tax_id proveedor": doc.get("proveedor_tax_id"),
            "número": doc.get("numero_factura"),
            "fecha": doc.get("fecha_emision"),
            "vencimiento": _vencimiento(doc),
            "moneda": doc.get("moneda"),
            "neto": doc.get("importe_neto"),
            "impuestos": doc.get("importe_iva"),
            "total": doc.get("importe_total"),
            "método pago": doc.get("metodo_pago"),
            "referencias": _referencias(doc),
            "confianza": f"{float(r.confidence)*100:.0f}%",
            "ausentes": len(missing_fields(r)),
            "advertencias": len(r.warnings),
        })
    return rows


def render_summary_table(results) -> None:
    st.dataframe(pd.DataFrame(_summary_rows(results)),
                 use_container_width=True, hide_index=True)


def render_download(results) -> None:
    st.download_button(
        "Descargar extracción CSV",
        data=results_csv(results).encode("utf-8-sig"),
        file_name="ap-control-tower-extraccion.csv",
        mime="text/csv",
        use_container_width=True,
    )


def render_detail(results) -> None:
    for r in results:
        doc = r.document
        label, kind = TYPE_LABELS.get(doc.get("document_type"), ("Documento", "mut"))
        engine_label = ("Document AI"
                        if r.engine == "google_document_ai_invoice_parser" else "motor local")
        with st.expander(r.doc_id, expanded=False):
            st.html(
                f"{badge(label, kind)} &nbsp; "
                f"{badge('confianza ' + str(r.confidence), 'ok' if not r.warnings else 'flag')} &nbsp; "
                f"{badge(engine_label, 'info' if r.engine == 'google_document_ai_invoice_parser' else 'mut')}"
                f"<div style='margin-top:8px;color:#5A6572;font-size:13px;'>"
                f"{r.pages} página(s) · {r.text_chars} caracteres extraídos"
                f"{('<br><b>Advertencias:</b> ' + ' | '.join(r.warnings)) if r.warnings else ''}"
                f"</div>",
            )
            rows = []
            for field in DETAIL_FIELDS:
                fc = r.field_confidences.get(field)
                value = _cell(doc.get(field))
                rows.append({
                    "campo": field,
                    "valor": value,
                    "confianza": "" if fc is None else str(fc),
                    "estado": "encontrado" if _is_present(doc.get(field)) else "ausente",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            miss = missing_fields(r)
            if miss:
                st.caption("Campos ausentes: " + ", ".join(miss))


def render_session_audit(audit) -> None:
    """Audit trail temporal (en memoria) de la sesion."""
    events = audit.events
    if not events:
        st.caption("Sin eventos todavía.")
        return
    rows = [{
        "#": ev.seq,
        "hora (UTC)": ev.ts,
        "acción": ev.action,
        "documento": ev.invoice_id or "",
        "resultado": ev.result or "",
    } for ev in events]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    ok = audit.verify_chain()
    st.caption(f"Cadena de auditoría {'íntegra ✓' if ok else 'inconsistente ✗'} · "
               "vive solo en esta sesión (no se guarda en disco ni base).")
