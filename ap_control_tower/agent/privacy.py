"""Minimización y enmascaramiento antes de salir del entorno de Brand UP."""

from __future__ import annotations

import re
from typing import Any

from ..persistence.masking import mask_account, mask_iban, mask_tax_id

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[\s-]?[A-Z0-9]){10,30}\b", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"(?<!\w)\d(?:[\s-]?\d){9,25}(?!\w)")
_MAX_TEXT = 500


def _masked_match(match: re.Match[str]) -> str:
    compact = re.sub(r"[\s-]", "", match.group(0))
    if len(compact) <= 4:
        return "*" * len(compact)
    return "*" * (len(compact) - 4) + compact[-4:]


def redact_text(value: Any, *, max_length: int = _MAX_TEXT) -> str:
    """Redacta identificadores largos y acota texto no confiable."""
    text = str(value or "").replace("\x00", " ").strip()
    text = _IBAN.sub(lambda match: mask_iban(match.group(0)) or "[enmascarado]", text)
    text = _LONG_NUMBER.sub(_masked_match, text)
    if len(text) > max_length:
        text = text[: max_length - 1].rstrip() + "…"
    return text


def mask_reference(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def safe_document_fields(document: dict[str, Any]) -> dict[str, Any]:
    """Subconjunto mínimo del documento para una explicación operativa."""
    bank_value = document.get("iban") or document.get("proveedor_cuenta_bancaria")
    bank_masked = (
        mask_iban(document.get("iban"))
        or mask_account(document.get("proveedor_cuenta_bancaria"))
    )
    return {
        "tipo_documental": document.get("document_type"),
        "proveedor": redact_text(
            document.get("proveedor_nombre_comercial")
            or document.get("proveedor_razon_social_legal")
            or "No identificado",
            max_length=120,
        ),
        "id_fiscal_proveedor": mask_tax_id(document.get("proveedor_tax_id")),
        "numero_factura": mask_reference(document.get("numero_factura")),
        "fecha_emision": document.get("fecha_emision"),
        "fecha_vencimiento": (
            document.get("fecha_vencimiento_calculada")
            or document.get("fecha_vencimiento_texto")
        ),
        "moneda": document.get("moneda"),
        "importe_total": document.get("importe_total"),
        "importe_neto": document.get("importe_neto"),
        "importe_iva": document.get("importe_iva"),
        "tipo_iva": redact_text(document.get("tipo_iva"), max_length=100) or None,
        "condiciones_pago": redact_text(
            document.get("condiciones_pago"), max_length=160
        )
        or None,
        "referencia_oc": mask_reference(document.get("po_reference")),
        "datos_bancarios_presentes": bool(bank_value),
        "datos_bancarios_enmascarados": bank_masked,
    }
