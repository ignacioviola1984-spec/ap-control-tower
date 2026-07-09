"""Pipeline maker-checker: procesa el mes completo, factura por factura.

Orden de etapas por factura (un hard que falla corta ahi y manda la factura
a la cola de excepciones SIN intervencion humana; un soft registra flag y
la factura sigue):

  intake -> C1 completitud -> C2 duplicados -> C3 autorizacion OC
        -> C4 imputacion (maker propone, checker valida; soft)
        -> C5 match con tolerancias (hard/soft segun materialidad)
        -> C6 datos bancarios vs maestro (hard + alerta fraude)
        -> contabilizacion simulada (ERP) -> C7 conciliacion pre-pago
        -> asignacion a lote del primer jueves posterior a la recepcion

MonthRunner procesa de a UNA factura (process_next), para que la UI muestre
al motor trabajando en vivo; run_month() lo drena entero de una vez.

El gate humano de liberacion del lote NO vive aca: el pipeline solo llega
hasta "en_lote". Liberar dinero siempre es humano.
"""

from __future__ import annotations

from decimal import Decimal

from ..audit import AuditTrail
from ..config import DEFAULT_CONFIG, EXCEPTION_OWNERS, Controls, EngineConfig
from ..envutil import resolve_commit
from ..models import (
    ControlResult,
    Dataset,
    ExceptionItem,
    Invoice,
    InvoiceOutcome,
    PaymentBatch,
    RunResult,
    SEVERITY_SOFT,
    STATUS_BLOQUEADA,
    STATUS_EN_LOTE,
    STATUS_PROXIMO_CICLO,
)
from .controls import (
    RunContext,
    check_autorizacion_oc,
    check_completitud,
    check_conciliacion,
    check_datos_bancarios,
    check_duplicados,
    check_match,
    checker_validate_imputacion,
    maker_propose_imputacion,
)

FLAG_BY_CONTROL_SOFT = {
    Controls.C5_MATCH: "MATCH_TOLERANCIA_MENOR",
    Controls.C4_IMPUTACION: "IMPUTACION_OBSERVADA",
}


def _next_thursday(after, config: EngineConfig):
    """Primer jueves de pago ESTRICTAMENTE posterior a la fecha dada; None si no queda."""
    for thu in config.payment_thursdays:
        if thu > after:
            return thu
    return None


class MonthRunner:
    """Corre el mes de a una factura por vez, en orden cronologico de recepcion.

    Uso incremental (UI):  while (step := runner.process_next()): ...
    Uso de una vez:        run_month(dataset) drena el runner completo.
    Deterministico: mismo dataset -> mismos resultados.
    """

    def __init__(self, dataset: Dataset, config: EngineConfig = DEFAULT_CONFIG,
                 run_id: str | None = None) -> None:
        self.dataset = dataset
        self.config = config
        self.audit = AuditTrail(run_id=run_id, commit=resolve_commit())
        self.ctx = RunContext(dataset=dataset, config=config)
        self.outcomes: dict[str, InvoiceOutcome] = {}
        self.exceptions: list[ExceptionItem] = []
        self.carryover: list[str] = []
        self._batch_map: dict = {}
        self._invoices = list(dataset.invoices)  # ya ordenadas por recepcion
        self._pos = 0
        self._finalized: RunResult | None = None
        self.audit.add(agent="orquestador", action="inicio-corrida",
                       evidence={"mes": config.demo_month, "facturas": len(self._invoices)})

    # ------------------------------------------------------------------
    @property
    def total_invoices(self) -> int:
        return len(self._invoices)

    @property
    def processed_count(self) -> int:
        return self._pos

    def process_next(self) -> tuple[Invoice, InvoiceOutcome] | None:
        """Procesa la siguiente factura; None cuando el mes esta completo."""
        if self._pos >= len(self._invoices):
            return None
        inv = self._invoices[self._pos]
        self._pos += 1
        outcome = self._process(inv)
        return inv, outcome

    # ------------------------------------------------------------------
    def _audit_control(self, inv: Invoice, res: ControlResult) -> None:
        self.audit.add(
            agent=res.checker,
            action=f"control:{res.control_id}",
            invoice_id=inv.invoice_id,
            control_id=res.control_id,
            result=("pasa" if res.passed
                    else ("falla-hard" if res.severity == "hard" else "flag-soft")),
            evidence={"detalle": res.detail, **res.evidence},
        )

    def _process(self, inv: Invoice) -> InvoiceOutcome:
        ctx, audit, dataset = self.ctx, self.audit, self.dataset
        results: list[ControlResult] = []
        flags: list[str] = []
        blocking: ControlResult | None = None

        audit.add(agent="maker-ingesta", action="intake-email",
                  invoice_id=inv.invoice_id,
                  evidence={"proveedor": inv.vendor_name, "numero": inv.invoice_number,
                            "importe": str(inv.amount_total), "moneda": inv.currency,
                            "recibida": inv.received_date.isoformat()})

        # --- C1 completitud (hard, corta antes de registrar nada) ---
        res = check_completitud(inv, ctx)
        results.append(res); self._audit_control(inv, res)
        if not res.passed:
            blocking = res
        else:
            # --- C2 duplicados (hard) contra la historia ya ingresada ---
            res = check_duplicados(inv, ctx)
            results.append(res); self._audit_control(inv, res)
            if not res.passed:
                blocking = res

        # La factura que paso completitud+duplicados entra al registro operativo.
        # Si ya existia una carga manual heredada en el Excel, se respeta tal
        # cual esta (no se pisa): la divergencia la detecta C7, no la esconde
        # el registro. El humano ya no tipea.
        if blocking is None:
            manual = inv.cashflow_amount_manual
            ctx.cashflow[inv.invoice_id] = {
                "amount": manual if manual is not None else inv.amount_total,
                "vendor": inv.vendor_id,
                "estado": "en proceso de pago", "disputa": False,
                "fuente": "carga manual previa (Excel heredado)" if manual is not None
                          else "registrado por el agente",
            }
            audit.add(agent="maker-registro", action="registro-cashflow",
                      invoice_id=inv.invoice_id,
                      evidence={"importe": str(ctx.cashflow[inv.invoice_id]["amount"]),
                                "fuente": ctx.cashflow[inv.invoice_id]["fuente"]})

            # --- C3 autorizacion de OC (hard) ---
            res = check_autorizacion_oc(inv, ctx)
            results.append(res); self._audit_control(inv, res)
            if not res.passed:
                blocking = res

        if blocking is None:
            po = dataset.pos[inv.po_ref]

            # --- C4 imputacion: maker propone, checker independiente valida (soft) ---
            proposal = maker_propose_imputacion(inv, po)
            audit.add(agent="maker-imputacion", action="propuesta-imputacion",
                      invoice_id=inv.invoice_id, evidence=proposal)
            res = checker_validate_imputacion(inv, proposal, ctx)
            results.append(res); self._audit_control(inv, res)
            if not res.passed:
                flags.append(FLAG_BY_CONTROL_SOFT[Controls.C4_IMPUTACION])
            if res.evidence.get("clasificacion") == "intercompany":
                flags.append("INTERCOMPANY")

            # --- C5 match con tolerancias (hard/soft) ---
            res = check_match(inv, ctx)
            results.append(res); self._audit_control(inv, res)
            if not res.passed:
                if res.severity == SEVERITY_SOFT:
                    flags.append(FLAG_BY_CONTROL_SOFT[Controls.C5_MATCH])
                else:
                    blocking = res

        if blocking is None:
            # --- C6 datos bancarios vs maestro (hard + alerta fraude) ---
            res = check_datos_bancarios(inv, ctx)
            results.append(res); self._audit_control(inv, res)
            if not res.passed:
                blocking = res

        if blocking is None:
            # Contabilizacion simulada en el ERP (maker contable) y match confirmado
            ctx.erp[inv.invoice_id] = {
                "amount": inv.amount_total, "status": "contabilizada", "matched": True,
                "gl_account": dataset.pos[inv.po_ref].gl_account,
            }
            audit.add(agent="maker-contable", action="contabilizacion-erp",
                      invoice_id=inv.invoice_id,
                      evidence={"importe": str(inv.amount_total),
                                "cuenta": dataset.pos[inv.po_ref].gl_account})

            # --- C7 conciliacion pre-pago cashflow vs ERP (hard) ---
            res = check_conciliacion(inv, ctx)
            results.append(res); self._audit_control(inv, res)
            if not res.passed:
                blocking = res

        # ---- resolucion de la factura ----
        if blocking is not None:
            # Si habia entrado al cashflow, queda marcada bloqueada (no se borra historia)
            if inv.invoice_id in ctx.cashflow:
                ctx.cashflow[inv.invoice_id]["estado"] = "bloqueada"
            fraud = blocking.control_id == Controls.C6_DATOS_BANCARIOS
            self.exceptions.append(ExceptionItem(
                invoice_id=inv.invoice_id,
                control_id=blocking.control_id,
                severity=blocking.severity,
                owner=EXCEPTION_OWNERS.get(blocking.control_id, "AP"),
                detail=blocking.detail,
                evidence=blocking.evidence,
                fraud_alert=fraud,
            ))
            audit.add(agent="orquestador", action="a-cola-de-excepciones",
                      invoice_id=inv.invoice_id, control_id=blocking.control_id,
                      result="bloqueada",
                      evidence={"dueno_sugerido": EXCEPTION_OWNERS.get(blocking.control_id, "AP"),
                                "alerta_fraude": fraud})
            outcome = InvoiceOutcome(
                invoice_id=inv.invoice_id, status=STATUS_BLOQUEADA,
                blocking_control=blocking.control_id, flags=sorted(set(flags)),
                batch_date=None, control_results=results,
            )
        else:
            # Consumo de OC solo para facturas totalmente limpias de hard
            ctx.po_consumed[inv.po_ref] = (
                ctx.po_consumed.get(inv.po_ref, Decimal("0")) + inv.amount_total
            )
            batch_date = _next_thursday(inv.received_date, self.config)
            if batch_date is None:
                self.carryover.append(inv.invoice_id)
                status, bdate = STATUS_PROXIMO_CICLO, None
                audit.add(agent="orquestador", action="programada-proximo-ciclo",
                          invoice_id=inv.invoice_id, result="proximo_ciclo",
                          evidence={"motivo": "sin jueves de pago restante en el mes"})
            else:
                b = self._batch_map.setdefault(batch_date, PaymentBatch(
                    batch_date=batch_date, invoice_ids=[], total=Decimal("0")))
                b.invoice_ids.append(inv.invoice_id)
                b.total += inv.amount_total
                status, bdate = STATUS_EN_LOTE, batch_date
                audit.add(agent="orquestador", action="asignada-a-lote",
                          invoice_id=inv.invoice_id, result="en_lote",
                          evidence={"lote": batch_date.isoformat(),
                                    "importe": str(inv.amount_total),
                                    "flags": sorted(set(flags))})
            outcome = InvoiceOutcome(
                invoice_id=inv.invoice_id, status=status,
                blocking_control=None, flags=sorted(set(flags)),
                batch_date=bdate, control_results=results,
            )

        self.outcomes[inv.invoice_id] = outcome
        # Toda factura ingresada (pase o no) queda en la historia para duplicados
        ctx.ingested.append(inv)
        return outcome

    # ------------------------------------------------------------------
    def finalize(self) -> RunResult:
        """Cierra la corrida (procesa lo pendiente si quedara) y arma el RunResult."""
        if self._finalized is not None:
            return self._finalized
        while self.process_next() is not None:
            pass
        batches = [self._batch_map[d] for d in sorted(self._batch_map)]
        self.audit.add(agent="orquestador", action="fin-corrida",
                       evidence={"lotes": {b.batch_date.isoformat(): str(b.total) for b in batches},
                                 "bloqueadas": sum(1 for o in self.outcomes.values()
                                                   if o.status == STATUS_BLOQUEADA),
                                 "proximo_ciclo": len(self.carryover)})
        self._finalized = RunResult(
            run_id=self.audit.run_id, commit=self.audit.commit, outcomes=self.outcomes,
            batches=batches, exceptions=self.exceptions, carryover_ids=self.carryover,
        )
        return self._finalized


def run_month(dataset: Dataset, config: EngineConfig = DEFAULT_CONFIG,
              run_id: str | None = None) -> tuple[RunResult, AuditTrail, RunContext]:
    """Corre el mes completo de una vez. Deterministico.

    Devuelve tambien el RunContext (cashflow/ERP/consumos) porque los checkers
    de lote (engine/batch.py) revalidan contra ese estado.
    """
    runner = MonthRunner(dataset, config=config, run_id=run_id)
    result = runner.finalize()
    return result, runner.audit, runner.ctx
