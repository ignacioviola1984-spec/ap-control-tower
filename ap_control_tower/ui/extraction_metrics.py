"""Métricas descriptivas de extracción, sin dependencias de presentación.

Se separan de ``ui/components/extraction_view.py`` a propósito: ese módulo es
una vista heredada que arrastra ``ui/theme.py`` (el sistema visual anterior) y
llamadas a APIs de Streamlit ya deprecadas. La página de Indicadores sólo
necesitaba estos cálculos, y al importarla entera cargaba el sistema visual
viejo dentro de la superficie activa. Acá viven los cálculos puros; la vista
heredada queda desconectada del producto.

Regla de honestidad conservada del original: NO se afirma «precisión» sin
validación humana. Se informan cobertura, confianza declarada por el extractor,
campos encontrados y ausentes.
"""

from __future__ import annotations

from ..extraction.schema import FIELD_ORDER

#: Campos de negocio para cobertura (se excluyen banderas internas).
_NON_BUSINESS = {"iban_enmascarado", "campos_ilegibles"}
BUSINESS_FIELDS = [field for field in FIELD_ORDER if field not in _NON_BUSINESS]


def _is_present(value) -> bool:
    return value not in (None, "", [], {})


def present_fields(result) -> list[str]:
    return [f for f in BUSINESS_FIELDS if _is_present(result.document.get(f))]


def missing_fields(result) -> list[str]:
    return [f for f in BUSINESS_FIELDS if not _is_present(result.document.get(f))]


def coverage(result) -> float:
    return len(present_fields(result)) / len(BUSINESS_FIELDS) if BUSINESS_FIELDS else 0.0


def _informed_confidences(results) -> list[float]:
    """Confianzas POR CAMPO informadas (para un promedio honesto)."""
    values: list[float] = []
    for result in results:
        values.extend(float(value) for value in (result.field_confidences or {}).values())
    return values


def aggregate_metrics(results, errors=None) -> dict:
    """Métricas descriptivas de extracción; no implican exactitud validada."""
    from .trial.workflow import (
        duplicate_doc_ids,
        requires_human_review,
        unique_results,
    )

    results = unique_results(results)
    total = len(results)
    found = sum(len(present_fields(r)) for r in results)
    missing = sum(len(missing_fields(r)) for r in results)
    informed = _informed_confidences(results)
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


__all__ = [
    "BUSINESS_FIELDS", "aggregate_metrics", "coverage", "missing_fields",
    "present_fields",
]
