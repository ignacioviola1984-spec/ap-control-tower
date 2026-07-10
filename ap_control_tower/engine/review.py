"""Revision humana: confirmaciones que cambian el estado real del sistema.

El humano interviene en DOS lugares: confirma datos aca, libera dinero en el
gate de pagos. Este modulo cubre lo primero y NUNCA lo segundo: confirmar
datos no puede liberar un pago (no toca aprobaciones ni liberaciones; el
lote al que se incorpore una factura confirmada vuelve a necesitar sus dos
sign-offs agenticos y la aprobacion humana del gate).

Cada accion exige nombre de quien confirma y queda en el audit trail con
timestamp, igual que el gate. Las transiciones invalidas levantan ReviewError.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from ..audit import AuditTrail
from ..config import DEFAULT_CONFIG, EXCEPTION_OWNERS, Controls, EngineConfig
from ..models import (
    ControlResult,
    Dataset,
    ExceptionItem,
    Invoice,
    PaymentBatch,
    RunResult,
    SEVERITY_SOFT,
    STATUS_ANTICIPO_EXCEPCION,
    STATUS_ANTICIPO_PENDIENTE,
    STATUS_ANTICIPO_RETENIDO,
    STATUS_BLOQUEADA,
    STATUS_DOMICILIACION,
    STATUS_EN_LOTE,
    STATUS_PENDIENTE_DATOS_INTERNOS,
    STATUS_PROXIMO_CICLO,
    STATUS_TARJETA,
    TareaConciliacion,
)
from .controls import (
    RunContext,
    check_anticipo,
    check_conciliacion,
    check_datos_bancarios,
    check_gobierno_non_po,
    check_mandato_domiciliacion,
    checker_validate_imputacion,
    maker_propose_imputacion_non_po,
)
from .pipeline import FLAG_BY_CONTROL_SOFT

AGENT = "revision-humana"


class ReviewError(RuntimeError):
    """Confirmacion invalida: falta un dato, un nombre, o el estado no lo admite."""


def _swap_invoice(dataset: Dataset, ctx: RunContext, new_inv: Invoice) -> None:
    """Reemplaza la factura en el dataset y en la historia de la corrida, para
    que toda revalidacion posterior (checker A del lote) vea los datos confirmados."""
    for i, inv in enumerate(dataset.invoices):
        if inv.invoice_id == new_inv.invoice_id:
            dataset.invoices[i] = new_inv
            break
    for i, inv in enumerate(ctx.ingested):
        if inv.invoice_id == new_inv.invoice_id:
            ctx.ingested[i] = new_inv
            break


def _audit_control(audit: AuditTrail, inv: Invoice, res: ControlResult) -> None:
    audit.add(agent=res.checker, action=f"control:{res.control_id}",
              invoice_id=inv.invoice_id, control_id=res.control_id,
              result=("pasa" if res.passed
                      else ("falla-hard" if res.severity == "hard" else "flag-soft")),
              evidence={"detalle": res.detail, **res.evidence})


def confirm_internal_data(
    dataset: Dataset, result: RunResult, ctx: RunContext, audit: AuditTrail,
    confirmed_by: str, invoice_id: str,
    cost_center: str, internal_approver: str, contract_ref: str,
    assignable_thursdays: list[date] | None = None,
    config: EngineConfig = DEFAULT_CONFIG,
) -> str:
    """Confirma (o corrige) los datos internos de una non-PO retenida.

    Aplica los valores confirmados, re-ejecuta los controles restantes y la
    factura sigue su flujo: lote del jueves (si hay uno asignable posterior a
    su recepcion), proximo ciclo, o tarea de conciliacion segun su metodo.
    Devuelve el estado final. JAMAS aprueba ni libera un lote.

    assignable_thursdays: jueves cuyos lotes siguen abiertos a incorporaciones
    (lo decide la capa que gestiona los workflows; un lote que cambia pierde
    sus sign-offs y vuelve a necesitar checkers + gate).
    """
    if not confirmed_by or not confirmed_by.strip():
        raise ReviewError("la confirmacion requiere el nombre de quien confirma")
    outcome = result.outcomes.get(invoice_id)
    if outcome is None or outcome.status != STATUS_PENDIENTE_DATOS_INTERNOS:
        raise ReviewError(f"{invoice_id} no esta pendiente de datos internos "
                          f"(estado: {outcome.status if outcome else 'inexistente'})")
    if not (cost_center or "").strip() or not (internal_approver or "").strip() \
            or not (contract_ref or "").strip():
        raise ReviewError("confirmar exige los tres datos: centro de coste, "
                          "aprobador interno y contrato/soporte")

    retencion = next(r for r in result.retenciones if r.invoice_id == invoice_id)
    inv_old = next(i for i in dataset.invoices if i.invoice_id == invoice_id)
    inv = replace(inv_old, cost_center=cost_center.strip(),
                  internal_approver=internal_approver.strip(),
                  contract_ref=contract_ref.strip())
    _swap_invoice(dataset, ctx, inv)

    audit.add(agent=AGENT, action="confirmacion-datos-internos",
              invoice_id=invoice_id, result="confirmado",
              evidence={"confirmado_por": confirmed_by.strip(),
                        "centro_de_coste": inv.cost_center,
                        "aprobador_interno": inv.internal_approver,
                        "contrato_soporte": inv.contract_ref,
                        "propuesta_original_del_agente": retencion.propuesta,
                        "nota": "confirmar datos NO libera pagos: el gate sigue siendo el gate"})

    results = list(outcome.control_results)
    flags = list(outcome.flags)
    blocking: ControlResult | None = None

    res = check_gobierno_non_po(inv, ctx)
    results.append(res); _audit_control(audit, inv, res)
    if not res.passed:  # no deberia ocurrir: los guards ya validaron
        raise ReviewError(f"gobierno non-PO sigue incompleto: {res.detail}")

    proposal = maker_propose_imputacion_non_po(inv, ctx)
    audit.add(agent="maker-imputacion", action="propuesta-imputacion",
              invoice_id=invoice_id, evidence=proposal)
    res = checker_validate_imputacion(inv, proposal, ctx)
    results.append(res); _audit_control(audit, inv, res)
    if not res.passed:
        flags.append(FLAG_BY_CONTROL_SOFT[Controls.C4_IMPUTACION])

    if inv.metodo_pago == "transferencia":
        res = check_datos_bancarios(inv, ctx)
        results.append(res); _audit_control(audit, inv, res)
        if not res.passed:
            blocking = res
    elif inv.metodo_pago == "domiciliacion_direct_debit":
        res = check_mandato_domiciliacion(inv, ctx)
        results.append(res); _audit_control(audit, inv, res)
        if not res.passed:
            blocking = res

    if blocking is None:
        ctx.erp[invoice_id] = {
            "amount": inv.amount_total, "status": "contabilizada", "matched": True,
            "gl_account": proposal["gl_account"],
            "tratamiento_iva": inv.tratamiento_iva,
        }
        audit.add(agent="maker-contable", action="contabilizacion-erp",
                  invoice_id=invoice_id,
                  evidence={"importe": str(inv.amount_total),
                            "cuenta": proposal["gl_account"],
                            "tratamiento_iva": inv.tratamiento_iva})
        res = check_conciliacion(inv, ctx)
        results.append(res); _audit_control(audit, inv, res)
        if not res.passed:
            blocking = res

    result.retenciones.remove(retencion)

    if blocking is not None:
        if invoice_id in ctx.cashflow:
            ctx.cashflow[invoice_id]["estado"] = "bloqueada"
        result.exceptions.append(ExceptionItem(
            invoice_id=invoice_id, control_id=blocking.control_id,
            severity=blocking.severity,
            owner=EXCEPTION_OWNERS.get(blocking.control_id, "AP"),
            detail=blocking.detail, evidence=blocking.evidence,
            fraud_alert=blocking.control_id == Controls.C6_DATOS_BANCARIOS))
        outcome.status = STATUS_BLOQUEADA
        outcome.blocking_control = blocking.control_id
        outcome.flags = sorted(set(flags))
        outcome.control_results = results
        audit.add(agent="orquestador", action="a-cola-de-excepciones",
                  invoice_id=invoice_id, control_id=blocking.control_id,
                  result="bloqueada", evidence={})
        return outcome.status

    if inv.metodo_pago == "domiciliacion_direct_debit":
        status, bdate = STATUS_DOMICILIACION, None
        result.tareas.append(TareaConciliacion(
            invoice_id=invoice_id, tipo="post_debito",
            detail="Conciliar el cargo bancario del debito contra el asiento"))
    elif inv.metodo_pago == "tarjeta":
        status, bdate = STATUS_TARJETA, None
        result.tareas.append(TareaConciliacion(
            invoice_id=invoice_id, tipo="extracto_tarjeta",
            detail="Conciliar contra el extracto mensual de la tarjeta"))
    else:
        assignable = sorted(assignable_thursdays or [])
        bdate = next((t for t in assignable if t > inv.received_date), None)
        if bdate is None:
            status = STATUS_PROXIMO_CICLO
            result.carryover_ids.append(invoice_id)
            audit.add(agent="orquestador", action="programada-proximo-ciclo",
                      invoice_id=invoice_id, result=status,
                      evidence={"motivo": "sin lote del jueves abierto posterior a la recepcion"})
        else:
            status = STATUS_EN_LOTE
            batch = next((b for b in result.batches if b.batch_date == bdate), None)
            if batch is None:
                batch = PaymentBatch(batch_date=bdate, invoice_ids=[], total=Decimal("0"))
                result.batches.append(batch)
                result.batches.sort(key=lambda b: b.batch_date)
            batch.invoice_ids.append(invoice_id)
            batch.total += inv.amount_total
            audit.add(agent="orquestador", action="asignada-a-lote",
                      invoice_id=invoice_id, result="en_lote",
                      evidence={"lote": bdate.isoformat(),
                                "importe": str(inv.amount_total),
                                "nota": "lote reabierto: pierde sign-offs y vuelve al gate"})
    if invoice_id in ctx.cashflow:
        ctx.cashflow[invoice_id]["estado"] = "en proceso de pago"
    if inv.po_ref:
        ctx.po_consumed[inv.po_ref] = (
            ctx.po_consumed.get(inv.po_ref, Decimal("0")) + inv.amount_total)

    outcome.status = status
    outcome.batch_date = bdate
    outcome.flags = sorted(set(flags))
    outcome.control_results = results
    return status


def approve_anticipo(dataset: Dataset, result: RunResult, ctx: RunContext,
                     audit: AuditTrail, confirmed_by: str, invoice_id: str) -> str:
    """Aprueba internamente el presupuesto de una proforma retenida.

    Registra quien y cuando. La proforma sigue en su flujo propio (pendiente
    de factura final, o excepcion C8 si el anticipo ya se pago sin factura).
    Nunca entra a un lote de pago."""
    if not confirmed_by or not confirmed_by.strip():
        raise ReviewError("aprobar el anticipo requiere el nombre de quien aprueba")
    outcome = result.outcomes.get(invoice_id)
    if outcome is None or outcome.status != STATUS_ANTICIPO_RETENIDO:
        raise ReviewError(f"{invoice_id} no es un anticipo retenido sin aprobacion "
                          f"(estado: {outcome.status if outcome else 'inexistente'})")

    inv_old = next(i for i in dataset.invoices if i.invoice_id == invoice_id)
    inv = replace(inv_old, presupuesto_aprobado=True)
    _swap_invoice(dataset, ctx, inv)
    retencion = next((r for r in result.retenciones if r.invoice_id == invoice_id), None)
    if retencion is not None:
        result.retenciones.remove(retencion)

    audit.add(agent=AGENT, action="aprobacion-anticipo",
              invoice_id=invoice_id, result="presupuesto_aprobado",
              evidence={"aprobado_por": confirmed_by.strip(),
                        "importe": str(inv.amount_total),
                        "nota": "aprobacion interna del presupuesto; nunca libera pagos"})

    res = check_anticipo(inv, ctx)
    _audit_control(audit, inv, res)
    if not res.passed:
        result.exceptions.append(ExceptionItem(
            invoice_id=invoice_id, control_id=Controls.C8_ANTICIPO_SIN_FACTURA_FINAL,
            severity=res.severity,
            owner=EXCEPTION_OWNERS[Controls.C8_ANTICIPO_SIN_FACTURA_FINAL],
            detail=res.detail, evidence=res.evidence))
        outcome.status = STATUS_ANTICIPO_EXCEPCION
    else:
        outcome.status = STATUS_ANTICIPO_PENDIENTE
    outcome.control_results = list(outcome.control_results) + [res]
    return outcome.status
