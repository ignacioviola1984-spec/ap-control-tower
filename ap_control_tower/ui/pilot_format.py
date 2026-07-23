"""Presentación pura y localizada para el piloto operativo."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from .trial import workflow


DOCUMENT_TYPE_LABELS = {
    "invoice": "Factura fiscal",
    "proforma_or_advance_request": "Proforma o anticipo",
    "other": "Otro documento",
}

STATE_LABELS = {
    "processed": "Procesado",
    "pending_review": "Requiere revisión",
    "retained": "Retenido",
    "eligible": "Elegible",
    "approved": "Aprobado para propuesta",
    "rejected": "Rechazado",
    "excluded": "Excluido",
    "error": "Error",
}

STATE_ICONS = {
    "processed": ":material/check_circle:",
    "pending_review": ":material/rule:",
    "retained": ":material/pause_circle:",
    "eligible": ":material/task_alt:",
    "approved": ":material/verified:",
    "rejected": ":material/cancel:",
    "excluded": ":material/block:",
    "error": ":material/error:",
}

CODE_LABELS = {
    **STATE_LABELS,
    "confirmed": "Datos confirmados",
    "payment_exception_approved": "Excepción autorizada",
    "requested": "Revisión solicitada",
    "ok": "Correcto",
    "con-advertencias": "Con advertencias",
    "omitido": "Omitido",
    "sesion-iniciada": "Sesión iniciada",
    "sesion-cerrada": "Sesión cerrada",
    "ingesta": "Ingreso de documentos",
    "documento-procesado": "Documento procesado",
    "documento-repetido-omitido": "Documento repetido omitido",
    "error-procesamiento": "Error de procesamiento",
    "maestro-proveedores-sage-cargado": "Maestro de proveedores Sage cargado",
    "proveedor-vinculado-sage": "Proveedor vinculado con Sage",
    "proveedor-vinculado-por-similitud-nombre": "Proveedor vinculado por similitud de nombre",
    "proveedor-ambiguo-sage": "Proveedor ambiguo en Sage",
    "proveedor-no-encontrado-sage": "Proveedor no encontrado en Sage",
    "fyi": "Informativo",
    "revision-confirmada": "Revisión confirmada",
    "revision-retenida": "Documento retenido",
    "excepcion-pago-autorizada": "Excepción autorizada",
    "propuesta-pago-decidida": "Decisión sobre propuesta de pago",
}


def label_for_code(value) -> str:
    """Convierte códigos internos en texto legible sin perder trazabilidad."""
    text = str(value or "").strip()
    if not text:
        return "—"
    return CODE_LABELS.get(
        text,
        text.replace("_", " ").replace("-", " ").capitalize(),
    )


def format_date(value) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    try:
        return datetime.fromisoformat(text[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return text


def format_datetime(value) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        zone = parsed.strftime(" %Z") if parsed.tzinfo else ""
        return parsed.strftime("%d/%m/%Y %H:%M") + zone
    except ValueError:
        return text


def decimal_value(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def format_amount(value, currency: str | None = None) -> str:
    amount = decimal_value(value)
    if amount is None:
        return "—" if not currency else f"{currency} —"
    rendered = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{str(currency or '—').upper()} {rendered}"


def supplier_name(document: dict) -> str:
    return str(
        document.get("proveedor_razon_social_legal")
        or document.get("proveedor_nombre_comercial")
        or "—"
    )


def document_state(result, review_decisions: dict, approval_decisions: dict,
                   duplicate_ids: set[str] | None = None) -> tuple[str, list[str]]:
    doc_id = result.doc_id
    payment = approval_decisions.get(doc_id) or {}
    if payment.get("status") in {"approved", "rejected", "excluded"}:
        status = str(payment["status"])
        reasons = [str(payment.get("note"))] if payment.get("note") else []
        return status, reasons

    review = review_decisions.get(doc_id) or {}
    reasons = workflow.review_reasons(
        result,
        duplicate=bool(duplicate_ids and doc_id in duplicate_ids),
        classification_requested=review.get("status") == "requested",
    )
    if review.get("status") == "retained":
        return "retained", reasons + ([str(review.get("note"))] if review.get("note") else [])
    approval = workflow.approval_state(
        result, review_decisions, approval_decisions, duplicate_ids or set()
    )
    if approval["status"] == "eligible":
        return "eligible", []
    if reasons:
        return "pending_review", list(dict.fromkeys(reasons))
    return "processed", list(dict.fromkeys(approval.get("reasons") or []))


def priority_for(reasons: list[str]) -> tuple[int, str]:
    joined = " ".join(reasons).casefold()
    if any(term in joined for term in ("apócrif", "padrón de arca", "duplicad")):
        return 0, "Alta"
    if any(term in joined for term in ("campos críticos", "importe", "clasificación")):
        return 1, "Media"
    return 2, "Normal"


def totals_by_currency(items) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for item in items:
        result = item.get("result") if isinstance(item, dict) else item
        document = result.document
        amount = decimal_value(document.get("importe_total"))
        if amount is None:
            continue
        currency = str(document.get("moneda") or "Sin moneda").upper()
        totals[currency] = totals.get(currency, Decimal("0")) + amount
    return totals


def format_totals(totals: dict[str, Decimal]) -> str:
    return " · ".join(format_amount(amount, currency) for currency, amount in sorted(totals.items())) or "—"


def operational_summary(session) -> dict[str, int]:
    results = workflow.unique_results(session.results)
    duplicates = workflow.duplicate_doc_ids(results)
    states = [
        document_state(
            result, session.review_decisions, session.approval_decisions, duplicates
        )[0]
        for result in results
    ]
    return {
        "received": len(results) + len(session.errors),
        "processed": len(results),
        "pending_review": states.count("pending_review"),
        "warnings": sum(1 for result in results if result.warnings),
        "eligible": states.count("eligible"),
        "retained_or_excluded": sum(
            states.count(status) for status in ("retained", "rejected", "excluded")
        ),
        "approved": states.count("approved"),
        "errors": len(session.errors),
    }
