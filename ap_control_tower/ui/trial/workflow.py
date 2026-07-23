"""Reglas puras del circuito real: revisión humana y propuesta de pago.

No integra ERP ni banco. Una aprobación significa únicamente que el documento
queda incluido en una propuesta controlada de pago.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from ...matching import meets_fuzzy_threshold
from ...sage.vendor_master import FUZZY_VENDOR_FYI


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


def missing_payment_fields(document: dict) -> list[str]:
    """Datos mínimos para proponer un pago, incluso si es un anticipo."""
    missing = [field for field in ("moneda", "importe_total")
               if not present(document.get(field))]
    if not (present(document.get("proveedor_nombre_comercial")) or
            present(document.get("proveedor_razon_social_legal"))):
        missing.append("proveedor")
    return missing


# Advertencias informativas (FYI): se muestran y se auditan, pero no derivan
# a revisión humana. Decisión funcional 2026-07-14: la mayoría de las facturas
# reales se pagan por débito directo; que el extractor no estructure datos
# bancarios visibles no requiere aprobación humana, solo flag.
FYI_WARNINGS = (
    "hay datos bancarios visibles pero no se pudieron estructurar",
    FUZZY_VENDOR_FYI,
    # Controles ARCA con AP_ARCA_FAIL_MODE=warn: la falta de verificación se
    # muestra y se audita, pero no deriva (el sufijo marca la variante aviso).
    "modo aviso: no deriva",
)

# Motivos de los controles ARCA (C10 padrón / C11 APOC). Derivan a revisión
# humana sin importar el tipo documental: un proveedor apócrifo debe verse en
# la cola aunque el documento sea una proforma. Los textos los emite
# controls/arca/validators.py; acá solo se reconocen por frases distintivas
# (nunca "arca" solo: "marca" lo contiene).
ARCA_WARNING_MARKERS = (
    "padrón de arca",
    "facturas apócrifas de arca",
    "verificación contra padrón arca",
    "dígito verificador inválido",
    "incoherente con el tipo de comprobante",
)


def _arca_warnings(result) -> list[str]:
    relevant: list[str] = []
    for item in result.warnings or []:
        lowered = str(item).casefold()
        if any(str(fyi).casefold() in lowered for fyi in FYI_WARNINGS):
            continue
        if any(marker in lowered for marker in ARCA_WARNING_MARKERS):
            relevant.append(str(item))
    return relevant

KNOWN_CURRENCIES = {
    "EUR", "USD", "GBP", "CHF", "MXN", "CAD", "JPY",
    "SEK", "NOK", "DKK", "PLN", "BRL", "ARS",
}


def _amounts_reconcile(document: dict) -> bool:
    """Neto + IVA = total (tolerancia 0.02): validación determinista."""
    try:
        net = Decimal(str(document["importe_neto"]))
        tax = Decimal(str(document["importe_iva"]))
        total = Decimal(str(document["importe_total"]))
    except (InvalidOperation, TypeError, KeyError):
        return False
    return abs((net + tax) - total) <= Decimal("0.02")


def _plausible_date(value) -> bool:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return False
    return 2000 <= parsed.year <= 2035


def _field_validated(document: dict, field: str) -> bool:
    """Una validación determinista aprobada pesa más que un score de confianza.

    Si el valor extraído pasa un chequeo objetivo, la baja confianza del
    extractor no deriva por sí sola (decisión funcional 2026-07-14, basada en
    la corrida run1: la confianza del extractor no correlaciona con la
    exactitud real).
    """
    if not present(document.get(field)):
        return False
    if field in {"importe_neto", "importe_iva", "importe_total", "tipo_iva"}:
        return _amounts_reconcile(document)
    if field == "moneda":
        return str(document["moneda"]).strip().upper() in KNOWN_CURRENCIES
    if field in {"fecha_emision", "fecha_vencimiento_calculada"}:
        return _plausible_date(document.get(field))
    if field in {"numero_factura", "document_type",
                 "proveedor_nombre_comercial", "proveedor_razon_social_legal"}:
        return True
    return False


def _field_warnings(result) -> list[str]:
    relevant: list[str] = []
    document = result.document
    for item in result.warnings or []:
        text = str(item)
        lowered = text.casefold()
        if "document ai" in lowered:
            continue
        if any(str(fyi).casefold() in lowered for fyi in FYI_WARNINGS):
            continue
        if lowered.startswith("baja confianza en:"):
            fields = [field.strip() for field in text.split(":", 1)[1].split(",")]
            fields = [field for field in fields
                      if field in CRITICAL_CONFIDENCE_FIELDS
                      and not _field_validated(document, field)]
            if fields:
                relevant.append("baja confianza en: " + ", ".join(fields))
            continue
        relevant.append(text)
    return relevant


def review_reasons(result, *, duplicate: bool = False,
                   classification_requested: bool = False) -> list[str]:
    """Política canónica. La ausencia de OC y una proforma clara no son errores."""
    document = result.document
    # Los motivos ARCA van primero: APOC es la señal de máxima severidad y
    # debe leerse antes que cualquier otro motivo. dict.fromkeys al final
    # evita duplicarlos cuando también pasan por _field_warnings.
    reasons: list[str] = _arca_warnings(result)
    doc_type = document.get("document_type")
    if classification_requested:
        reasons.append("revisión solicitada por clasificación documental")
    if doc_type == "other" or not present(doc_type):
        reasons.append("documento no reconocido o clasificación dudosa")
    elif doc_type == "invoice":
        missing = missing_critical_fields(document)
        if missing:
            reasons.append("campos críticos ausentes: " + ", ".join(missing))
        # Las señales ARCA no son advertencias del extractor: no habilitan el
        # camino de baja confianza de otros campos.
        extractor_flagged = any(
            "document ai" not in lowered
            and not any(marker in lowered for marker in ARCA_WARNING_MARKERS)
            and not any(str(fyi).casefold() in lowered for fyi in FYI_WARNINGS)
            for lowered in (str(item).casefold()
                            for item in (result.warnings or [])))
        # Una confianza técnica aislada no alcanza: debe existir además una
        # advertencia explícita del extractor, y el campo dudoso no debe
        # superar su validación determinista (aritmética, formato, plausibilidad).
        # run1 demostró que el score de confianza no correlaciona con exactitud.
        low = [field for field, confidence in (result.field_confidences or {}).items()
               if extractor_flagged and field in CRITICAL_CONFIDENCE_FIELDS
               and Decimal(str(confidence)) < REVIEW_THRESHOLD
               and present(document.get(field))
               and not _field_validated(document, field)]
        if low:
            reasons.append("baja confianza en campo crítico: " + ", ".join(sorted(low)))
        # Fix run2/GD-119: un importe no positivo clasificado como invoice es
        # casi siempre una nota de crédito. Nunca debe tratarse como factura
        # a pagar sin que un humano confirme el tipo documental.
        try:
            if present(document.get("importe_total")) and \
                    Decimal(str(document["importe_total"])) <= 0:
                reasons.append("importe no positivo: posible nota de crédito, "
                               "requiere confirmación humana del tipo documental")
        except InvalidOperation:
            reasons.append("importe total ilegible")
        reasons.extend(_field_warnings(result))
    elif doc_type == "proforma_or_advance_request":
        reasons.append("proforma / anticipo: requiere autorización humana para pago")
    if duplicate:
        reasons.append("posible factura duplicada")
    return list(dict.fromkeys(reasons))


def requires_human_review(result, *, duplicate: bool = False,
                          classification_requested: bool = False) -> bool:
    return bool(review_reasons(
        result, duplicate=duplicate,
        classification_requested=classification_requested))


def unique_results(results) -> list:
    """Devuelve una sola instancia por documento, conservando el orden original.

    ``doc_id`` identifica el documento dentro de una corrida. Repetir el mismo
    ``doc_id`` por un rerun/importación doble no es un duplicado comercial.
    """
    unique = []
    seen: set[str] = set()
    for result in results:
        doc_id = str(result.doc_id)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        unique.append(result)
    return unique


NEAR_DUP_AMOUNT_TOLERANCE = Decimal("0.05")
# El umbral fuzzy canonico vive en ``ap_control_tower.matching`` y se comparte
# con la vinculacion de proveedores. Series cortas recurrentes quedan debajo;
# un correlativo largo con un digito cambiado queda por encima.


def _amount_of(doc: dict) -> Decimal | None:
    try:
        return Decimal(str(doc.get("importe_total")))
    except (InvalidOperation, TypeError):
        return None


def duplicate_doc_ids(results) -> set[str]:
    """Duplicados exactos (proveedor+número) y casi-duplicados.

    Fix run2/GD-107: un casi-duplicado (número correlativo o con typo, importe
    igual o con diferencia de céntimos) evadía el matching exacto. Regla:
    mismo proveedor + importe dentro de la tolerancia + números similares.
    Costo asumido y documentado: una factura recurrente legítima de importe
    idéntico y numeración correlativa (ej. cuota mensual) puede marcarse para
    revisión; es preferible a pagar un duplicado real.
    """
    flagged: set[str] = set()
    by_supplier: dict[str, list] = {}
    exact: dict[tuple[str, str], list[str]] = {}
    for result in unique_results(results):
        doc = result.document
        supplier = (doc.get("proveedor_tax_id") or
                    doc.get("proveedor_razon_social_legal") or
                    doc.get("proveedor_nombre_comercial"))
        number = doc.get("numero_factura")
        if not present(supplier):
            continue
        skey = str(supplier).strip().casefold()
        by_supplier.setdefault(skey, []).append(result)
        if present(number):
            key = (skey, str(number).strip().casefold())
            exact.setdefault(key, []).append(result.doc_id)
    flagged.update(doc_id for ids in exact.values() if len(ids) > 1
                   for doc_id in ids)

    for _, group in by_supplier.items():
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                amount_a, amount_b = _amount_of(a.document), _amount_of(b.document)
                if amount_a is None or amount_b is None:
                    continue
                if abs(amount_a - amount_b) > NEAR_DUP_AMOUNT_TOLERANCE:
                    continue
                num_a = str(a.document.get("numero_factura") or "").strip().casefold()
                num_b = str(b.document.get("numero_factura") or "").strip().casefold()
                similar = (
                    not num_a or not num_b
                    or meets_fuzzy_threshold(num_a, num_b)
                )
                if similar:
                    flagged.update({a.doc_id, b.doc_id})
    return flagged


def review_queue(results, decisions: dict,
                 approval_decisions: dict | None = None) -> list[dict]:
    duplicates = duplicate_doc_ids(results)
    queue = []
    approval_decisions = approval_decisions or {}
    for result in unique_results(results):
        decision = decisions.get(result.doc_id) or {}
        requested = decision.get("status") == "requested"
        reasons = review_reasons(
            result, duplicate=result.doc_id in duplicates,
            classification_requested=requested)
        if (approval_decisions.get(result.doc_id) or {}).get("status") in {
                "rejected", "excluded"}:
            reasons.append("decisión previa fuera de propuesta: requiere revisión humana")
        # Las decisiones históricas creadas por una política anterior se
        # conservan en auditoría, pero no fuerzan su reaparición en la cola.
        if reasons:
            queue.append({"result": result, "reasons": reasons,
                          "decision": decision,
                          "pending": decision.get("status") not in {
                              "confirmed", "retained", "payment_exception_approved"}})
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
    review = review_decisions.get(doc_id) or {}
    exception_approved = review.get("status") == "payment_exception_approved"
    if doc.get("document_type") != "invoice" and not exception_approved:
        reasons.append("Proforma / solicitud de anticipo: no es una factura fiscal "
                       "y no puede incorporarse a una propuesta de pago de facturas.")
    elif doc.get("document_type") != "invoice":
        reasons.extend("falta " + field for field in missing_payment_fields(doc))
    else:
        reasons.extend("falta " + field for field in missing_critical_fields(doc))
        try:
            if present(doc.get("importe_total")) and Decimal(str(doc["importe_total"])) <= 0:
                reasons.append("importe no positivo")
        except InvalidOperation:
            reasons.append("importe inválido")
    if duplicates and doc_id in duplicates:
        reasons.append("posible duplicado")
    needs_review = requires_human_review(
        result, duplicate=bool(duplicates and doc_id in duplicates),
        classification_requested=review.get("status") == "requested")
    if needs_review:
        if review.get("status") not in {"confirmed", "payment_exception_approved"}:
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
            for result in unique_results(results)]
