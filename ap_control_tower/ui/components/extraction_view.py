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
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO

import pandas as pd
import streamlit as st

from ...extraction.schema import FIELD_ORDER
from ...persistence.masking import mask_tax_id
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


def process_one(name, data):
    """Procesa UN PDF inline y mide su tiempo. -> (result|None, error|None, segundos)."""
    from time import perf_counter

    from ...app import process_uploaded_document
    t0 = perf_counter()
    try:
        result = process_uploaded_document(name, data)
        return result, None, perf_counter() - t0
    except Exception as exc:  # red/API: mensaje claro, no crash
        return None, str(exc), perf_counter() - t0


# Advertencias que son NOTAS DE MODO (no problemas de campo): no disparan
# "Revisar campos". La ausencia de OC NO genera advertencias -> ruta non-PO normal.
_MODE_NOTES = ("Document AI no configurado", "Document AI no disponible")

# Estados permitidos del documento en la sesion.
STATUS_PROCESADO = "Procesado"
STATUS_REVISAR = "Revisar campos"
STATUS_NO_RECONOCIDO = "Documento no reconocido"
STATUS_ERROR = "Error de procesamiento"

_STATUS_KIND = {
    STATUS_PROCESADO: "ok",
    STATUS_REVISAR: "flag",
    STATUS_NO_RECONOCIDO: "mut",
    STATUS_ERROR: "block",
}


def field_warnings(result) -> list:
    """Advertencias de CAMPO (excluye notas de modo del extractor)."""
    return [w for w in result.warnings if not any(n in w for n in _MODE_NOTES)]


def status_label(result, *, duplicate: bool = False) -> str:
    """Estado del documento. La ausencia de OC NO produce 'Revisar campos'
    (no genera advertencias): es ruta non-PO normal."""
    from ..trial.workflow import requires_human_review

    if result.document.get("document_type") == "other":
        return STATUS_NO_RECONOCIDO
    return (STATUS_REVISAR if requires_human_review(result, duplicate=duplicate)
            else STATUS_PROCESADO)


def route_label(doc: dict) -> str:
    """Ruta operativa: con OC referenciada = PO; sin OC = non-PO (normal)."""
    return "PO" if doc.get("po_reference") else "non-PO"


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


EXPORT_COLUMNS = [
    "archivo", "tipo documental", "proveedor", "tax ID proveedor", "número",
    "fecha de emisión", "vencimiento", "moneda", "importe neto", "tipo IVA",
    "importe IVA", "importe total", "referencia OC", "ruta PO/non-PO",
    "confianza", "estado", "advertencias",
]


def _export_rows(results) -> list[dict]:
    from ..trial.workflow import duplicate_doc_ids, unique_results

    results = unique_results(results)
    duplicates = duplicate_doc_ids(results)
    rows = []
    for result in results:
        doc = result.document
        rows.append({
            "archivo": result.doc_id,
            "tipo documental": doc.get("document_type") or "",
            "proveedor": (doc.get("proveedor_razon_social_legal") or
                          doc.get("proveedor_nombre_comercial") or ""),
            "tax ID proveedor": mask_tax_id(doc.get("proveedor_tax_id")) or "",
            "número": doc.get("numero_factura") or "",
            "fecha de emisión": doc.get("fecha_emision") or "",
            "vencimiento": _vencimiento(doc),
            "moneda": doc.get("moneda") or "",
            "importe neto": doc.get("importe_neto"),
            "tipo IVA": doc.get("tipo_iva") or "",
            "importe IVA": doc.get("importe_iva"),
            "importe total": doc.get("importe_total"),
            "referencia OC": doc.get("po_reference") or "",
            "ruta PO/non-PO": route_label(doc),
            "confianza": float(result.confidence),
            "estado": status_label(result, duplicate=result.doc_id in duplicates),
            "advertencias": " | ".join(str(item) for item in (result.warnings or [])),
        })
    return rows


def results_csv(results) -> str:
    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    writer.writerows(_export_rows(results))
    return out.getvalue()


def _excel_value(column: str, value):
    if value in (None, ""):
        return None
    if column in {"fecha de emisión", "vencimiento"}:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)
    if column in {"importe neto", "importe IVA", "importe total"}:
        try:
            return float(Decimal(str(value).replace(",", ".")))
        except (InvalidOperation, ValueError):
            return str(value)
    return value


def results_excel(results) -> bytes:
    rows = [{column: _excel_value(column, row.get(column))
             for column in EXPORT_COLUMNS} for row in _export_rows(results)]
    frame = pd.DataFrame(rows, columns=EXPORT_COLUMNS)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl",
                        date_format="YYYY-MM-DD", datetime_format="YYYY-MM-DD") as writer:
        frame.to_excel(writer, sheet_name="Extracción", index=False)
        sheet = writer.book["Extracción"]
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = cell.font.copy(bold=True)
        for column_cells in sheet.columns:
            width = min(45, max(12, max(len(str(cell.value or ""))
                                        for cell in column_cells) + 2))
            sheet.column_dimensions[column_cells[0].column_letter].width = width
    return output.getvalue()


# ------------------------------------------------------------------ render
def _informed_confidences(results) -> list:
    """Todas las confianzas POR CAMPO informadas (para el promedio honesto)."""
    vals: list = []
    for r in results:
        vals.extend(float(v) for v in r.field_confidences.values())
    return vals


def aggregate_metrics(results, errors=None) -> dict:
    """Metricas descriptivas de extraccion; no implican exactitud validada."""
    from ..trial.workflow import unique_results

    results = unique_results(results)
    total = len(results)
    found = sum(len(present_fields(r)) for r in results)
    missing = sum(len(missing_fields(r)) for r in results)
    informed = _informed_confidences(results)
    from ..trial.workflow import duplicate_doc_ids, requires_human_review

    duplicates = duplicate_doc_ids(results)
    return {
        "documents": total + len(errors or []),   # procesados = intentados
        "ok": total,
        "invoices": sum(1 for r in results
                        if r.document.get("document_type") == "invoice"),
        "fields_found": found,
        "fields_missing": missing,
        "coverage": found / (found + missing) if found + missing else 0.0,
        # Confianza PROMEDIO solo sobre campos con confianza informada.
        "confidence": (sum(informed) / len(informed)) if informed else None,
        "with_warnings": sum(1 for r in results
                             if requires_human_review(
                                 r, duplicate=r.doc_id in duplicates)),
        "errors": len(errors or []),
    }


def render_metrics(results, processing_seconds=None, errors=None) -> None:
    m = aggregate_metrics(results, errors)
    r1 = st.columns(3)
    r1[0].metric("Documentos procesados", m["documents"])
    r1[1].metric("Facturas reconocidas", m["invoices"])
    r1[2].metric("Campos encontrados", m["fields_found"],
                 help="Cobertura: campos hallados sobre el esquema. Mide cobertura, no exactitud.")
    r2 = st.columns(3)
    r2[0].metric("Documentos con advertencias", m["with_warnings"])
    r2[1].metric("Confianza promedio",
                 "—" if m["confidence"] is None else f"{m['confidence']*100:.0f}%",
                 help="Promedio SOLO sobre campos con confianza informada. No implica exactitud validada.")
    r2[2].metric("Tiempo de procesamiento",
                 "—" if processing_seconds is None else f"{processing_seconds:.1f} s",
                 help="Tiempo acumulado de procesamiento en esta sesión.")
    st.caption("No se afirma exactitud sin validación humana: son cobertura, "
               "confianza y advertencias del extractor.")


def _summary_rows(results, errors=None) -> list[dict]:
    from ..trial.workflow import duplicate_doc_ids, unique_results

    results = unique_results(results)
    duplicates = duplicate_doc_ids(results)
    rows = []
    for r in results:
        doc = r.document
        rows.append({
            "archivo": r.doc_id,
            "tipo": doc.get("document_type"),
            "proveedor": doc.get("proveedor_nombre_comercial"),
            "número": doc.get("numero_factura"),
            "fecha": doc.get("fecha_emision"),
            "vencimiento": _vencimiento(doc),
            "moneda": doc.get("moneda"),
            "total": doc.get("importe_total"),
            "ruta PO/non-PO": route_label(doc),
            "confianza": f"{float(r.confidence)*100:.0f}%",
            "estado": status_label(r, duplicate=r.doc_id in duplicates),
        })
    for name, _detail in (errors or []):
        rows.append({
            "archivo": name, "tipo": "—", "proveedor": "—", "número": "—",
            "fecha": "—", "vencimiento": "—", "moneda": "—", "total": "—",
            "ruta PO/non-PO": "—", "confianza": "—", "estado": STATUS_ERROR,
        })
    return rows


def render_summary_table(results, errors=None) -> None:
    st.dataframe(pd.DataFrame(_summary_rows(results, errors)),
                 use_container_width=True, hide_index=True)


def render_download(results, *, key: str) -> None:
    csv_col, excel_col = st.columns(2)
    csv_col.download_button(
        "Descargar extracción CSV", data=results_csv(results).encode("utf-8-sig"),
        file_name="ap-control-tower-extraccion.csv", mime="text/csv",
        use_container_width=True, key=f"{key}_csv")
    excel_col.download_button(
        "Descargar extracción Excel", data=results_excel(results),
        file_name="ap-control-tower-extraccion.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True, key=f"{key}_xlsx")


def render_detail(results, audit=None, proc_seconds=None) -> None:
    from ..trial.workflow import duplicate_doc_ids, unique_results

    results = unique_results(results)
    duplicates = duplicate_doc_ids(results)
    for r in results:
        doc = r.document
        label, kind = TYPE_LABELS.get(doc.get("document_type"), ("Documento", "mut"))
        engine_label = ("Document AI"
                        if r.engine == "google_document_ai_invoice_parser" else "motor local")
        estado = status_label(r, duplicate=r.doc_id in duplicates)
        secs = None if proc_seconds is None else proc_seconds.get(r.doc_id)
        with st.expander(f"{r.doc_id}  ·  {estado}", expanded=False):
            st.html(
                f"{badge(label, kind)} &nbsp; "
                f"{badge(estado, _STATUS_KIND.get(estado, 'mut'))} &nbsp; "
                f"{badge('ruta ' + route_label(doc), 'info')} &nbsp; "
                f"{badge('confianza ' + str(r.confidence), 'ok' if not r.warnings else 'flag')} &nbsp; "
                f"{badge(engine_label, 'info' if r.engine == 'google_document_ai_invoice_parser' else 'mut')}"
                f"<div style='margin-top:8px;color:#5A6572;font-size:13px;'>"
                f"{r.pages} página(s) · {r.text_chars} caracteres"
                f"{f' · procesado en {secs:.1f}s' if secs is not None else ''}"
                f"</div>",
            )
            # Todos los datos extraidos + confianza por campo + estado del campo.
            rows = []
            for field in BUSINESS_FIELDS:
                fc = r.field_confidences.get(field)
                rows.append({
                    "campo": field,
                    "valor": _cell(doc.get(field)),
                    "confianza": "" if fc is None else str(fc),
                    "estado": "encontrado" if _is_present(doc.get(field)) else "ausente",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            miss = missing_fields(r)
            if miss:
                st.caption("Campos ausentes: " + ", ".join(miss))
            if r.warnings:
                st.caption("Advertencias: " + " | ".join(r.warnings))
            st.caption(f"Motor: {engine_label}"
                       + (f" · Tiempo de procesamiento: {secs:.1f}s" if secs is not None else ""))
            if audit is not None:
                doc_events = [e for e in audit.events if e.invoice_id == r.doc_id]
                if doc_events:
                    st.caption("Audit trail del documento (sesión):")
                    st.dataframe(pd.DataFrame([{
                        "#": e.seq, "hora (UTC)": e.ts, "acción": e.action,
                        "resultado": e.result or "",
                    } for e in doc_events]), use_container_width=True, hide_index=True)


def render_session_audit(audit, persisted: bool = False) -> None:
    """Audit trail encadenado de la sesión activa o de una corrida guardada."""
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
    location = ("guardada en PostgreSQL" if persisted
                else "activa en esta sesión")
    st.caption(f"Cadena de auditoría {'íntegra ✓' if ok else 'inconsistente ✗'} · "
               f"{location}.")
