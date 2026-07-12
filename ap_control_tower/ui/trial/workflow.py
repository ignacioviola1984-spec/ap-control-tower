"""Reglas puras del circuito real: revisión humana y propuesta de pago.

No integra ERP ni banco. Una aprobación significa únicamente que el documento
queda incluido en una propuesta controlada de pago.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


REVIEW_THRESHOLD = Decimal("0.75")
EDITABLE_FIELDS = (
    "proveedor_nombre_comercial",
    "numero_factura",
    "fecha_emision",
    "fecha_vencimiento_calculada",
    "moneda",
    "importe_total",
    "po_reference",
)
REVIEW_RELEVANT_FIELDS = set(EDITABLE_FIELDS) | {
    "proveedor_razon_social_legal", "proveedor_tax_id", "cliente_tax_id",
    "tipo_iva", "importe_neto", "importe_iva", "condiciones_pago",
    "iban", "bic", "metodo_pago",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def present(value) -> bool:
    return value is not None and str(value).strip() not in {"", "None", "null", "—"}


def missing_critical_fields(document: dict) -> list[str]:
    missing = [field for field in ("numero_factura", "fecha_emision", "moneda",
                                    "importe_total") if not present(document.get(field))]
    if not (present(document.get("proveedor_nombre_comercial")) or
            present(document.get("proveedor_razon_social_legal"))):
        missing.append("proveedor")
    return missing


def _field_warnings(result) -> list[str]:
    relevant: list[str] = []
    for item in result.warnings or []:
        text = str(item)
        lowered = text.casefold()
        if "document ai" in lowered:
            continue
        if lowered.startswith("baja confianza en:"):
            fields = [field.strip() for field in text.split(":", 1)[1].split(",")]
            fields = [field for field in fields if field in REVIEW_RELEVANT_FIELDS]
            if fields:
                relevant.append("baja confianza en: " + ", ".join(fields))
            continue
        relevant.append(text)
    return relevant


def review_reasons(result) -> list[str]:
    """Motivos que requieren juicio humano. La ausencia de OC no es motivo."""
    document = result.document
    reasons: list[str] = []
    doc_type = document.get("document_type")
    if doc_type != "invoice":
        reasons.append("tipo documental distinto de factura fiscal")
    missing = missing_critical_fields(document)
    if missing:
        reasons.append("campos críticos ausentes: " + ", ".join(missing))
    low = [field for field, confidence in (result.field_confidences or {}).items()
           if field in EDITABLE_FIELDS and Decimal(str(confidence)) < REVIEW_THRESHOLD
           and present(document.get(field))]
    if low:
        reasons.append("baja confianza: " + ", ".join(sorted(low)))
    reasons.extend(_field_warnings(result))
    return list(dict.fromkeys(reasons))


def duplicate_doc_ids(results) -> set[str]:
    groups: dict[tuple[str, str], list[str]] = {}
    for result in results:
        doc = result.document
        supplier = (doc.get("proveedor_tax_id") or
                    doc.get("proveedor_razon_social_legal") or
                    doc.get("proveedor_nombre_comercial"))
        number = doc.get("numero_factura")
        if present(supplier) and present(number):
            key = (str(supplier).strip().casefold(), str(number).strip().casefold())
            groups.setdefault(key, []).append(result.doc_id)
    return {doc_id for ids in groups.values() if len(ids) > 1 for doc_id in ids}


def review_queue(results, decisions: dict) -> list[dict]:
    duplicates = duplicate_doc_ids(results)
    queue = []
    for result in results:
        reasons = review_reasons(result)
        if result.doc_id in duplicates:
            reasons.append("posible factura duplicada")
        decision = decisions.get(result.doc_id) or {}
        if reasons or decision:
            queue.append({"result": result, "reasons": reasons,
                          "decision": decision,
                          "pending": decision.get("status") not in {"confirmed", "retained"}})
    return queue


def normalized_updates(updates: dict) -> dict:
    clean = {field: value.strip() if isinstance(value, str) else value
             for field, value in updates.items() if field in EDITABLE_FIELDS}
    if present(clean.get("importe_total")):
        try:
            amount = Decimal(str(clean["importe_total"]).replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError("El importe total debe ser numérico.") from exc
        if amount <= 0:
            raise ValueError("El importe total debe ser mayor que cero.")
        clean["importe_total"] = str(amount)
    if present(clean.get("moneda")):
        clean["moneda"] = str(clean["moneda"]).upper()
    return clean


def approval_state(result, review_decisions: dict, approval_decisions: dict,
                   duplicates: set[str] | None = None) -> dict:
    doc_id = result.doc_id
    existing = approval_decisions.get(doc_id) or {}
    if existing.get("status") in {"approved", "rejected"}:
        return {"status": existing["status"], "reasons": [], "decision": existing}

    reasons: list[str] = []
    doc = result.document
    if doc.get("document_type") != "invoice":
        reasons.append("no es una factura fiscal")
    reasons.extend("falta " + field for field in missing_critical_fields(doc))
    try:
        if present(doc.get("importe_total")) and Decimal(str(doc["importe_total"])) <= 0:
            reasons.append("importe no positivo")
    except InvalidOperation:
        reasons.append("importe inválido")
    if duplicates and doc_id in duplicates:
        reasons.append("posible duplicado")
    if review_reasons(result):
        review = review_decisions.get(doc_id) or {}
        if review.get("status") != "confirmed":
            reasons.append("revisión humana pendiente")
    return {"status": "retained" if reasons else "eligible",
            "reasons": list(dict.fromkeys(reasons)), "decision": existing}


def approval_rows(results, review_decisions: dict, approval_decisions: dict) -> list[dict]:
    duplicates = duplicate_doc_ids(results)
    return [{"result": result,
             **approval_state(result, review_decisions, approval_decisions, duplicates)}
            for result in results]
