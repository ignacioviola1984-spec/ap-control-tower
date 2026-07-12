"""Reglas puras del circuito real: revisión humana y propuesta de pago.

No integra ERP ni banco. Una aprobación significa únicamente que el documento
queda incluido en una propuesta controlada de pago.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


REVIEW_THRESHOLD = Decimal("0.65")
EDITABLE_FIELDS = (
    "document_type",
    "proveedor_nombre_comercial",
    "numero_factura",
    "fecha_emision",
    "fecha_vencimiento_calculada",
    "moneda",
    "importe_total",
    "po_reference",
)
CRITICAL_CONFIDENCE_FIELDS = {
    "document_type", "proveedor_nombre_comercial", "proveedor_razon_social_legal",
    "numero_factura", "fecha_emision", "moneda", "importe_total",
}
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


def review_reasons(result, *, duplicate: bool = False,
                   classification_requested: bool = False) -> list[str]:
    """Política canónica. La ausencia de OC y una proforma clara no son errores."""
    document = result.document
    reasons: list[str] = []
    doc_type = document.get("document_type")
    if classification_requested:
        reasons.append("revisión solicitada por clasificación documental")
    if doc_type == "other" or not present(doc_type):
        reasons.append("documento no reconocido o clasificación dudosa")
    elif doc_type == "invoice":
        missing = missing_critical_fields(document)
        if missing:
            reasons.append("campos críticos ausentes: " + ", ".join(missing))
        extractor_flagged = any(
            "document ai" not in str(item).casefold() for item in (result.warnings or []))
        # Una confianza técnica aislada no alcanza: debe existir además una
        # advertencia explícita del extractor. Evita derivar documentos sanos
        # por umbrales internos secundarios.
        low = [field for field, confidence in (result.field_confidences or {}).items()
               if extractor_flagged and field in CRITICAL_CONFIDENCE_FIELDS
               and Decimal(str(confidence)) < REVIEW_THRESHOLD
               and present(document.get(field))]
        if low:
            reasons.append("baja confianza en campo crítico: " + ", ".join(sorted(low)))
        reasons.extend(_field_warnings(result))
    # Una proforma correctamente clasificada se trata en elegibilidad, no como
    # error de extracción ni como revisión automática.
    if duplicate:
        reasons.append("posible factura duplicada")
    return list(dict.fromkeys(reasons))


def requires_human_review(result, *, duplicate: bool = False,
                          classification_requested: bool = False) -> bool:
    return bool(review_reasons(
        result, duplicate=duplicate,
        classification_requested=classification_requested))


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
        decision = decisions.get(result.doc_id) or {}
        requested = decision.get("status") == "requested"
        reasons = review_reasons(
            result, duplicate=result.doc_id in duplicates,
            classification_requested=requested)
        # Las decisiones históricas creadas por una política anterior se
        # conservan en auditoría, pero no fuerzan su reaparición en la cola.
        if reasons:
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
    if present(clean.get("document_type")) and clean["document_type"] not in {
            "invoice", "proforma_or_advance_request", "other"}:
        raise ValueError("Tipo documental inválido.")
    return clean


def approval_state(result, review_decisions: dict, approval_decisions: dict,
                   duplicates: set[str] | None = None) -> dict:
    doc_id = result.doc_id
    existing = approval_decisions.get(doc_id) or {}

    reasons: list[str] = []
    doc = result.document
    if doc.get("document_type") != "invoice":
        reasons.append("Proforma / solicitud de anticipo: no es una factura fiscal "
                       "y no puede incorporarse a una propuesta de pago de facturas.")
    else:
        reasons.extend("falta " + field for field in missing_critical_fields(doc))
        try:
            if present(doc.get("importe_total")) and Decimal(str(doc["importe_total"])) <= 0:
                reasons.append("importe no positivo")
        except InvalidOperation:
            reasons.append("importe inválido")
    if duplicates and doc_id in duplicates:
        reasons.append("posible duplicado")
    review = review_decisions.get(doc_id) or {}
    needs_review = requires_human_review(
        result, duplicate=bool(duplicates and doc_id in duplicates),
        classification_requested=review.get("status") == "requested")
    if needs_review:
        if review.get("status") != "confirmed":
            reasons.append("revisión humana pendiente")
    if existing.get("status") == "approved":
        return {"status": "approved", "reasons": [], "decision": existing}
    if existing.get("status") in {"rejected", "excluded"}:
        if existing.get("note"):
            reasons.append("decisión registrada: " + str(existing["note"]))
        return {"status": existing["status"],
                "reasons": list(dict.fromkeys(reasons)), "decision": existing}
    return {"status": "retained" if reasons else "eligible",
            "reasons": list(dict.fromkeys(reasons)), "decision": existing}


def approval_rows(results, review_decisions: dict, approval_decisions: dict) -> list[dict]:
    duplicates = duplicate_doc_ids(results)
    return [{"result": result,
             **approval_state(result, review_decisions, approval_decisions, duplicates)}
            for result in results]
