"""Workflow del lote de pago: doble sign-off agentico + gate humano.

Maquina de estados del lote (las transiciones invalidas levantan GateViolation):

    propuesto --checker A ok--> revalidado_a --checker B ok--> pendiente_aprobacion_humana
        |                            |
        +--checker falla-------------+--> detenido_por_checker

    pendiente_aprobacion_humana --approve(nombre)--> aprobado --release--> liberado_al_banco
    pendiente_aprobacion_humana / aprobado --reject(nombre, motivo)--> rechazado

Doctrina: los dos checkers son agentes; la aprobacion es EL unico gate humano
del sistema. "liberado_al_banco" es inalcanzable sin una aprobacion humana
registrada (nombre + decision + timestamp en el audit trail). El sistema se
auto-bloquea ante alertas. La aprobación para liberar dinero es siempre humana.

Checker A (revalidacion factura por factura): re-ejecuta TODOS los controles
de cada factura del lote contra el estado del mundo AL JUEVES del lote
(historia y consumos anteriores a esa fecha, excluyendo a la propia factura
para no compararse consigo misma).

Checker B (validacion del agregado): total del lote vs limite, limite por
proveedor, duplicados cruzados (proveedor+numero) dentro del lote y contra
los otros lotes de la corrida, moneda unica, y consistencia de totales.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ..audit import AuditTrail
from ..config import EngineConfig
from ..models import (
    Dataset,
    Invoice,
    PaymentBatch,
    RunResult,
    SEVERITY_HARD,
    STATUS_EN_LOTE,
    STATUS_LIBERADA_AL_BANCO,
    STATUS_LOTE_DEVUELTO,
)
from .controls import (
    RunContext,
    check_autorizacion_oc,
    check_completitud,
    check_conciliacion,
    check_datos_bancarios,
    check_duplicados,
    check_gobierno_non_po,
    check_match,
    check_vendor_master,
    checker_validate_imputacion,
    maker_propose_imputacion,
    maker_propose_imputacion_non_po,
)

# Estados del lote
ESTADO_PROPUESTO = "propuesto"
ESTADO_REVALIDADO_A = "revalidado_a"
ESTADO_PENDIENTE_HUMANO = "pendiente_aprobacion_humana"
ESTADO_APROBADO = "aprobado"
ESTADO_LIBERADO = "liberado_al_banco"
ESTADO_RECHAZADO = "rechazado"
ESTADO_DETENIDO = "detenido_por_checker"


class GateViolation(RuntimeError):
    """Transicion de estado invalida: alguien intento saltarse el gate."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class SignOff:
    checker: str
    ok: bool
    ts: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class HumanDecision:
    approver: str
    decision: str          # "aprobar" | "rechazar"
    ts: str
    reason: str = ""


def _revalidation_context(inv: Invoice, batch_date, result: RunResult,
                          ctx: RunContext) -> RunContext:
    """Estado del mundo al jueves del lote, excluyendo a la propia factura.

    - Historia de duplicados: solo lo recibido ANTES del jueves (el checker A
      corre ese dia; lo que llega despues no existe todavia).
    - Consumo de OC: solo facturas limpias recibidas antes del jueves, sin el
      consumo propio (una factura no puede agotarse el saldo a si misma).
    """
    ingested = [
        i for i in ctx.ingested
        if i.received_date < batch_date and i.invoice_id != inv.invoice_id
    ]
    consumed: dict[str, Decimal] = {}
    for i in ctx.ingested:
        if i.invoice_id == inv.invoice_id or i.received_date >= batch_date:
            continue
        outcome = result.outcomes.get(i.invoice_id)
        if outcome is not None and outcome.blocking_control is None and i.po_ref:
            consumed[i.po_ref] = consumed.get(i.po_ref, Decimal("0")) + i.amount_total
    return RunContext(
        dataset=ctx.dataset, config=ctx.config,
        ingested=ingested, po_consumed=consumed,
        cashflow=ctx.cashflow, erp=ctx.erp,
    )


def revalidate_invoice(inv: Invoice, batch_date, result: RunResult,
                       ctx: RunContext) -> list:
    """Checker A por factura: re-ejecuta TODOS los controles que aplican al
    documento segun su ruta (PO / non-PO) y metodo de pago."""
    sub = _revalidation_context(inv, batch_date, result, ctx)
    results = [check_completitud(inv, sub), check_duplicados(inv, sub),
               check_vendor_master(inv, sub)]
    if inv.po_ref is not None:
        results.append(check_autorizacion_oc(inv, sub))
        po = ctx.dataset.pos.get(inv.po_ref)
        if po is not None:
            proposal = maker_propose_imputacion(inv, po)
            results.append(checker_validate_imputacion(inv, proposal, sub))
            results.append(check_match(inv, sub))
    else:
        results.append(check_gobierno_non_po(inv, sub))
        proposal = maker_propose_imputacion_non_po(inv, sub)
        results.append(checker_validate_imputacion(inv, proposal, sub))
    if inv.metodo_pago == "transferencia":
        results.append(check_datos_bancarios(inv, sub))
    results.append(check_conciliacion(inv, sub))
    return results


class BatchWorkflow:
    """Ciclo de vida de UN lote del jueves, del propuesto a la liberacion."""

    def __init__(self, batch: PaymentBatch, result: RunResult, ctx: RunContext,
                 audit: AuditTrail, config: EngineConfig) -> None:
        self.batch = batch
        self.result = result
        self.ctx = ctx
        self.audit = audit
        self.config = config
        self.state = ESTADO_PROPUESTO
        self.sign_off_a: SignOff | None = None
        self.sign_off_b: SignOff | None = None
        self.human_decision: HumanDecision | None = None

    # ---------------- checker A: revalida cada factura ----------------
    def run_checker_a(self) -> SignOff:
        if self.state != ESTADO_PROPUESTO:
            raise GateViolation(f"checker A solo corre sobre un lote propuesto (estado: {self.state})")
        invoices = {i.invoice_id: i for i in self.ctx.dataset.invoices}
        hard_failures: list[dict[str, Any]] = []
        for inv_id in self.batch.invoice_ids:
            for res in revalidate_invoice(invoices[inv_id], self.batch.batch_date,
                                          self.result, self.ctx):
                if not res.passed and res.severity == SEVERITY_HARD:
                    hard_failures.append({
                        "factura": inv_id, "control": res.control_id, "detalle": res.detail,
                    })
        ok = not hard_failures
        self.sign_off_a = SignOff(
            checker="checker-lote-A (revalidacion factura por factura)",
            ok=ok, ts=_now(),
            detail=(f"{len(self.batch.invoice_ids)} facturas revalidadas contra los 7 controles"
                    if ok else f"{len(hard_failures)} fallas hard en revalidacion"),
            evidence={"facturas": len(self.batch.invoice_ids), "fallas": hard_failures},
        )
        self.state = ESTADO_REVALIDADO_A if ok else ESTADO_DETENIDO
        self.audit.add(agent="checker-lote-A", action="sign-off-lote",
                       control_id="LOTE_REVALIDACION",
                       result="firma" if ok else "detiene-lote",
                       evidence={"lote": self.batch.batch_date.isoformat(),
                                 **self.sign_off_a.evidence})
        return self.sign_off_a

    # ---------------- checker B: valida el agregado ----------------
    def run_checker_b(self) -> SignOff:
        if self.state != ESTADO_REVALIDADO_A:
            raise GateViolation(
                f"checker B requiere el sign-off previo del checker A (estado: {self.state})")
        invoices = {i.invoice_id: i for i in self.ctx.dataset.invoices}
        problems: list[str] = []

        amounts = [invoices[i].amount_total for i in self.batch.invoice_ids]
        if sum(amounts, Decimal("0")) != self.batch.total:
            problems.append("el total del lote no coincide con la suma de sus facturas")
        if self.batch.total > self.config.batch_max_total:
            problems.append(f"total {self.batch.total} supera el limite de lote "
                            f"{self.config.batch_max_total}")

        per_vendor: dict[str, Decimal] = {}
        for i in self.batch.invoice_ids:
            inv = invoices[i]
            per_vendor[inv.vendor_id] = per_vendor.get(inv.vendor_id, Decimal("0")) + inv.amount_total
        for vendor_id, total in sorted(per_vendor.items()):
            if total > self.config.batch_max_per_vendor:
                problems.append(f"proveedor {vendor_id} acumula {total} en el lote "
                                f"(limite {self.config.batch_max_per_vendor})")

        seen: dict[tuple[str, str], str] = {}
        for i in self.batch.invoice_ids:
            inv = invoices[i]
            key = (inv.vendor_id, inv.invoice_number)
            if key in seen:
                problems.append(f"duplicado cruzado dentro del lote: {i} y {seen[key]}")
            seen[key] = i
        for other in self.result.batches:
            if other.batch_date == self.batch.batch_date:
                continue
            for j in other.invoice_ids:
                inv_j = invoices[j]
                key = (inv_j.vendor_id, inv_j.invoice_number)
                if key in seen:
                    problems.append(f"duplicado cruzado contra el lote {other.batch_date}: "
                                    f"{seen[key]} vs {j}")

        currencies = {invoices[i].currency for i in self.batch.invoice_ids}
        if currencies - {self.config.base_currency}:
            problems.append(f"monedas fuera de la base {self.config.base_currency}: {currencies}")

        for i in self.batch.invoice_ids:
            o = self.result.outcomes[i]
            if o.status != STATUS_EN_LOTE or o.batch_date != self.batch.batch_date:
                problems.append(f"{i} no esta en estado en_lote para este jueves")

        ok = not problems
        self.sign_off_b = SignOff(
            checker="checker-lote-B (validacion del agregado)",
            ok=ok, ts=_now(),
            detail=("agregado validado: totales, limites por proveedor, duplicados cruzados, moneda"
                    if ok else "; ".join(problems)),
            evidence={"total": str(self.batch.total),
                      "facturas": len(self.batch.invoice_ids),
                      "por_proveedor": {k: str(v) for k, v in sorted(per_vendor.items())},
                      "problemas": problems},
        )
        self.state = ESTADO_PENDIENTE_HUMANO if ok else ESTADO_DETENIDO
        self.audit.add(agent="checker-lote-B", action="sign-off-lote",
                       control_id="LOTE_AGREGADO",
                       result="firma" if ok else "detiene-lote",
                       evidence={"lote": self.batch.batch_date.isoformat(),
                                 **self.sign_off_b.evidence})
        return self.sign_off_b

    # ---------------- EL gate humano ----------------
    def approve(self, approver: str) -> HumanDecision:
        if self.state != ESTADO_PENDIENTE_HUMANO:
            raise GateViolation(
                f"aprobar requiere lote pendiente de aprobacion humana (estado: {self.state})")
        if not (self.sign_off_a and self.sign_off_a.ok and self.sign_off_b and self.sign_off_b.ok):
            raise GateViolation("aprobar requiere los DOS sign-offs agenticos en verde")
        if not approver or not approver.strip():
            raise GateViolation("la aprobacion humana requiere el nombre del aprobador")
        self.human_decision = HumanDecision(approver=approver.strip(), decision="aprobar", ts=_now())
        self.state = ESTADO_APROBADO
        self.audit.add(agent="gate-humano", action="aprobacion-lote",
                       result="aprobado",
                       evidence={"lote": self.batch.batch_date.isoformat(),
                                 "aprobador": self.human_decision.approver,
                                 "decision": "aprobar",
                                 "timestamp_decision": self.human_decision.ts,
                                 "total": str(self.batch.total),
                                 "facturas": len(self.batch.invoice_ids)})
        return self.human_decision

    def reject(self, approver: str, reason: str) -> HumanDecision:
        if self.state not in (ESTADO_PENDIENTE_HUMANO, ESTADO_APROBADO):
            raise GateViolation(f"rechazar requiere lote pendiente o aprobado sin liberar "
                                f"(estado: {self.state})")
        if not approver or not approver.strip():
            raise GateViolation("el rechazo humano requiere el nombre de quien decide")
        if not reason or not reason.strip():
            raise GateViolation("el rechazo requiere un motivo")
        self.human_decision = HumanDecision(approver=approver.strip(), decision="rechazar",
                                            ts=_now(), reason=reason.strip())
        self.state = ESTADO_RECHAZADO
        for inv_id in self.batch.invoice_ids:
            self.result.outcomes[inv_id].status = STATUS_LOTE_DEVUELTO
        self.audit.add(agent="gate-humano", action="rechazo-lote",
                       result="rechazado",
                       evidence={"lote": self.batch.batch_date.isoformat(),
                                 "aprobador": self.human_decision.approver,
                                 "decision": "rechazar",
                                 "motivo": reason.strip(),
                                 "timestamp_decision": self.human_decision.ts,
                                 "facturas_devueltas": list(self.batch.invoice_ids)})
        return self.human_decision

    def release_to_bank(self) -> None:
        if self.state != ESTADO_APROBADO:
            raise GateViolation(f"liberar al banco requiere lote aprobado (estado: {self.state})")
        if (self.human_decision is None or self.human_decision.decision != "aprobar"
                or not self.human_decision.approver):
            raise GateViolation("liberar al banco requiere aprobacion humana registrada")
        self.state = ESTADO_LIBERADO
        for inv_id in self.batch.invoice_ids:
            self.result.outcomes[inv_id].status = STATUS_LIBERADA_AL_BANCO
        self.audit.add(agent="orquestador", action="liberacion-al-banco",
                       result="liberado",
                       evidence={"lote": self.batch.batch_date.isoformat(),
                                 "total": str(self.batch.total),
                                 "aprobado_por": self.human_decision.approver,
                                 "timestamp_aprobacion": self.human_decision.ts})
