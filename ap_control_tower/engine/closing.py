"""Cierre contable del lote liberado: conciliacion automatica pago vs pasivo.

Para cada factura del lote liberado: contabiliza el pago simulado, lo matchea
contra el pasivo registrado en la contabilizacion, y cancela el pasivo.
Cualquier inconsistencia va al reporte de excepciones de cierre: el humano
revisa excepciones, no el 100% (reemplaza el double-check del mismo equipo).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..audit import AuditTrail
from ..models import STATUS_CERRADA
from .batch import ESTADO_LIBERADO, BatchWorkflow, GateViolation
from .controls import RunContext


@dataclass
class ClosingReport:
    batch_date: str
    payments: list[dict[str, Any]] = field(default_factory=list)
    liabilities_cancelled: int = 0
    total_paid: Decimal = Decimal("0")
    exceptions: list[dict[str, Any]] = field(default_factory=list)


def close_batch(wf: BatchWorkflow, ctx: RunContext, audit: AuditTrail) -> ClosingReport:
    """Cierra un lote liberado. Requiere estado liberado_al_banco."""
    if wf.state != ESTADO_LIBERADO:
        raise GateViolation(f"el cierre requiere un lote liberado al banco (estado: {wf.state})")

    invoices = {i.invoice_id: i for i in ctx.dataset.invoices}
    report = ClosingReport(batch_date=wf.batch.batch_date.isoformat())

    for inv_id in wf.batch.invoice_ids:
        inv = invoices[inv_id]
        payment = {
            "invoice_id": inv_id,
            "vendor": inv.vendor_name,
            "amount": str(inv.amount_total),
            "value_date": wf.batch.batch_date.isoformat(),
            "reference": f"PAGO-{wf.batch.batch_date.isoformat()}-{inv_id}",
        }
        audit.add(agent="maker-cierre", action="contabilizacion-pago",
                  invoice_id=inv_id, evidence=payment)

        erp = ctx.erp.get(inv_id)
        if erp is None:
            report.exceptions.append({"factura": inv_id, "problema": "pago sin pasivo en el ERP"})
            audit.add(agent="checker-cierre", action="match-pago-pasivo",
                      invoice_id=inv_id, result="excepcion",
                      evidence={"problema": "pago sin pasivo en el ERP"})
            continue
        if erp["amount"] != inv.amount_total:
            report.exceptions.append({
                "factura": inv_id,
                "problema": f"pago {inv.amount_total} vs pasivo {erp['amount']}",
            })
            audit.add(agent="checker-cierre", action="match-pago-pasivo",
                      invoice_id=inv_id, result="excepcion",
                      evidence={"pago": str(inv.amount_total), "pasivo": str(erp["amount"])})
            continue

        erp["status"] = "pasivo_cancelado"
        report.payments.append(payment)
        report.liabilities_cancelled += 1
        report.total_paid += inv.amount_total
        wf.result.outcomes[inv_id].status = STATUS_CERRADA
        audit.add(agent="checker-cierre", action="match-pago-pasivo",
                  invoice_id=inv_id, result="conciliado",
                  evidence={"pago": str(inv.amount_total), "pasivo_cancelado": True})

    audit.add(agent="orquestador", action="cierre-lote",
              result="con-excepciones" if report.exceptions else "limpio",
              evidence={"lote": report.batch_date,
                        "pagos_conciliados": report.liabilities_cancelled,
                        "total_pagado": str(report.total_paid),
                        "excepciones": report.exceptions})
    return report
