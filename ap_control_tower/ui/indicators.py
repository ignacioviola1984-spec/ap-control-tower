"""Indicadores operativos calculados sobre la sesión.

Cada indicador declara qué mide, en qué unidad, sobre qué período y con qué
fuente. Un número sin definición en una pantalla de cuentas a pagar es una
invitación a discutir sobre qué significa en vez de sobre qué hacer.

Todo lo de acá es puro: se verifica sin levantar la interfaz. Cuando un
indicador no es calculable con los datos disponibles, vale ``None`` y la
pantalla muestra «—» con la razón. No se rellena con ceros ni con estimaciones.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .pilot_format import document_state
from .trial import workflow


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _iso_date(value) -> date | None:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


#: Tramos de antigüedad, en días desde la emisión.
AGING_BUCKETS = [(0, 30, "0–30"), (31, 60, "31–60"), (61, 90, "61–90"),
                 (91, 10_000, "90+")]


def aging_distribution(active, today: date | None = None) -> dict[str, int]:
    """Documentos por tramo de antigüedad desde la fecha de emisión."""
    today = today or date.today()
    salida = {label: 0 for _, _, label in AGING_BUCKETS}
    for result in workflow.unique_results(active.results):
        emitida = _iso_date(result.document.get("fecha_emision"))
        if emitida is None:
            continue
        dias = (today - emitida).days
        if dias < 0:
            dias = 0
        for desde, hasta, label in AGING_BUCKETS:
            if desde <= dias <= hasta:
                salida[label] += 1
                break
    return salida


def state_distribution(active) -> dict[str, int]:
    """Documentos por estado del circuito."""
    results = workflow.unique_results(active.results)
    duplicates = workflow.duplicate_doc_ids(results)
    salida: dict[str, int] = defaultdict(int)
    for result in results:
        state, _reasons = document_state(
            result, active.review_decisions, active.approval_decisions, duplicates
        )
        salida[state] += 1
    return dict(salida)


def due_distribution(active, today: date | None = None) -> dict[str, int]:
    """Documentos por horizonte de vencimiento."""
    today = today or date.today()
    salida = {"Vencido": 0, "0–7 días": 0, "8–30 días": 0, "31+ días": 0,
              "Sin fecha": 0}
    for result in workflow.unique_results(active.results):
        vence = _iso_date(result.document.get("fecha_vencimiento_calculada"))
        if vence is None:
            salida["Sin fecha"] += 1
            continue
        dias = (vence - today).days
        if dias < 0:
            salida["Vencido"] += 1
        elif dias <= 7:
            salida["0–7 días"] += 1
        elif dias <= 30:
            salida["8–30 días"] += 1
        else:
            salida["31+ días"] += 1
    return salida


def touchless_rate(active) -> float | None:
    """Proporción de documentos que llegaron a elegibles sin decisión humana.

    Es la métrica que dice cuánto trabajo se ahorró de verdad. Un documento que
    necesitó que una persona confirmara datos NO cuenta como touchless, aunque
    después haya salido bien.
    """
    results = workflow.unique_results(active.results)
    if not results:
        return None
    duplicates = workflow.duplicate_doc_ids(results)
    sin_intervencion = 0
    for result in results:
        doc_id = str(result.doc_id)
        if doc_id in active.review_decisions:
            continue
        reasons = workflow.review_reasons(result, duplicate=doc_id in duplicates)
        if not reasons:
            sin_intervencion += 1
    return sin_intervencion / len(results)


def human_review_rate(active) -> float | None:
    """Proporción de documentos derivados a revisión humana."""
    results = workflow.unique_results(active.results)
    if not results:
        return None
    duplicates = workflow.duplicate_doc_ids(results)
    derivados = sum(
        1 for result in results
        if workflow.review_reasons(
            result, duplicate=str(result.doc_id) in duplicates)
    )
    return derivados / len(results)


def exception_count(active) -> int:
    """Excepciones de pago autorizadas por una persona."""
    return sum(
        1 for decision in active.review_decisions.values()
        if decision.get("status") == "payment_exception_approved"
    )


def retained_amounts(active) -> dict[str, Decimal]:
    """Importe frenado por moneda (revisión, retención, rechazo o exclusión)."""
    results = workflow.unique_results(active.results)
    duplicates = workflow.duplicate_doc_ids(results)
    salida: dict[str, Decimal] = defaultdict(Decimal)
    for result in results:
        state, _reasons = document_state(
            result, active.review_decisions, active.approval_decisions, duplicates
        )
        if state not in {"retained", "pending_review", "rejected", "excluded"}:
            continue
        amount = _decimal(result.document.get("importe_total"))
        if amount is None or amount <= 0:
            continue
        salida[str(result.document.get("moneda") or "EUR").upper()] += amount
    return dict(salida)


def percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


__all__ = [
    "AGING_BUCKETS", "aging_distribution", "due_distribution",
    "exception_count", "human_review_rate", "percent", "retained_amounts",
    "state_distribution", "touchless_rate",
]
