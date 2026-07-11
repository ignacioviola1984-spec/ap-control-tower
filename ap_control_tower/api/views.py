"""Mapeo del estado de corrida a los modelos de respuesta (con enmascarado).

Ninguna vista incluye datos bancarios completos: el IBAN de la factura se
enmascara aca. La fase del ciclo de vida se deriva del estado operativo.
"""

from __future__ import annotations

from decimal import Decimal

from ..engine.lifecycle import phase_for_status
from ..models import STATUS_BLOQUEADA, Dataset
from ..persistence.masking import mask_iban
from .schemas import (
    BatchSummary,
    DocumentView,
    ExceptionView,
    MetricsView,
    RunSummary,
)


def _invoices(dataset: Dataset) -> dict:
    return {i.invoice_id: i for i in dataset.invoices}


def batch_summaries(run: dict) -> list[BatchSummary]:
    out = []
    for b in run["result"].batches:
        iso = b.batch_date.isoformat()
        wf = run["workflows"].get(iso)
        estado = ("cerrado" if iso in run["closing_reports"]
                  else (wf.state if wf else "propuesto"))
        out.append(BatchSummary(fecha_lote=iso, facturas=b.count,
                                total=str(b.total), moneda="EUR", estado=estado))
    return out


def run_summary(run: dict) -> RunSummary:
    result = run["result"]
    outcomes = result.outcomes.values()
    anticipos = sum(1 for o in outcomes if o.status.startswith("anticipo"))
    en_lote = sum(1 for o in outcomes if o.status == "en_lote")
    bloqueadas = sum(1 for o in outcomes if o.status == STATUS_BLOQUEADA)
    return RunSummary(
        run_id=result.run_id, commit=result.commit,
        documentos=len(result.outcomes), en_lote=en_lote, bloqueadas=bloqueadas,
        retenciones=len(result.retenciones), tareas_conciliacion=len(result.tareas),
        anticipos=anticipos, proximo_ciclo=len(result.carryover_ids),
        lotes=batch_summaries(run))


def document_view(dataset: Dataset, run: dict, invoice_id: str) -> DocumentView | None:
    invs = _invoices(dataset)
    inv = invs.get(invoice_id)
    outcome = run["result"].outcomes.get(invoice_id)
    if inv is None or outcome is None:
        return None
    ruta = ("anticipo" if outcome.status.startswith("anticipo")
            else ("po" if inv.po_ref else "non_po"))
    return DocumentView(
        invoice_id=invoice_id, proveedor=inv.vendor_name,
        numero_factura=inv.invoice_number, importe_total=str(inv.amount_total),
        moneda=inv.currency, estado=outcome.status,
        fase_ciclo_vida=phase_for_status(outcome.status),
        control_bloqueante=outcome.blocking_control, flags=list(outcome.flags),
        ruta=ruta, metodo_pago=inv.metodo_pago,
        lote=outcome.batch_date.isoformat() if outcome.batch_date else None,
        iban_enmascarado=mask_iban(inv.iban_on_invoice))


def all_document_views(dataset: Dataset, run: dict) -> list[DocumentView]:
    return [document_view(dataset, run, i.invoice_id) for i in dataset.invoices]


def exception_views(run: dict) -> list[ExceptionView]:
    return [ExceptionView(
        invoice_id=e.invoice_id, control_id=e.control_id, severidad=e.severity,
        owner=e.owner, detalle=e.detail, alerta_fraude=e.fraud_alert)
        for e in run["result"].exceptions]


def metrics_view(dataset: Dataset, run: dict) -> MetricsView:
    result = run["result"]
    outcomes = list(result.outcomes.values())
    total = len(outcomes) or 1
    bloqueadas = sum(1 for o in outcomes if o.status == STATUS_BLOQUEADA)
    en_lote = sum(1 for o in outcomes if o.status == "en_lote")
    con_flag = sum(1 for o in outcomes if o.flags)
    anticipos = sum(1 for o in outcomes if o.status.startswith("anticipo"))
    lotes_estado: dict[str, int] = {}
    for iso, wf in run["workflows"].items():
        estado = "cerrado" if iso in run["closing_reports"] else wf.state
        lotes_estado[estado] = lotes_estado.get(estado, 0) + 1
    total_lotes = sum((b.total for b in result.batches), Decimal("0"))
    return MetricsView(
        documentos=len(result.outcomes), en_lote=en_lote, bloqueadas=bloqueadas,
        retenciones=len(result.retenciones), tareas_conciliacion=len(result.tareas),
        anticipos=anticipos, con_flag=con_flag, proximo_ciclo=len(result.carryover_ids),
        tasa_revision_humana=round(len(result.retenciones) / total, 4),
        tasa_bloqueo=round(bloqueadas / total, 4),
        total_lotes=str(total_lotes), lotes_por_estado=lotes_estado)
