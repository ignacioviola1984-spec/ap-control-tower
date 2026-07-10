"""Pipeline maker-checker: procesa el mes completo, documento por documento.

Flujo por documento (un hard que falla corta ahi y manda el documento a la
cola de excepciones SIN intervencion humana; un soft registra flag y sigue;
una RETENCION deja el documento esperando datos internos, que es distinto de
un bloqueo por control):

  intake -> C0 clasificacion (etapa 0)
    proforma -> flujo de anticipos: exige aprobacion interna del presupuesto;
                anticipo pagado sin factura final posterior = excepcion (C8).
                Una proforma JAMAS entra al flujo de facturas ni a un lote.
    other    -> retencion "revision_manual"
    invoice  -> C1 completitud -> C2 duplicados -> registro cashflow
             -> C9 maestro de proveedores (incompleto = retencion alta)
             -> bifurcacion:
                  con po_ref  -> C3 autorizacion OC -> C4 (imputacion desde OC)
                              -> C5 match con tolerancias
                  sin po_ref  -> C10 gobierno non-PO (aprobador + centro de
                                 coste + soporte; el agente PROPONE por reglas
                                 proveedor->area, el humano confirma; si falta
                                 algo: retencion "pendiente de datos internos")
                              -> C4 (imputacion por reglas non-PO)
             -> por metodo de pago:
                  transferencia -> C6 IBAN vs maestro (fraude) -> asiento ->
                                   C7 conciliacion -> lote del jueves
                  domiciliacion -> C11 mandato SEPA -> asiento -> C7 ->
                                   tarea de conciliacion post-debito (SIN lote)
                  tarjeta       -> asiento -> C7 -> tarea de conciliacion
                                   contra extracto (SIN lote)

MonthRunner procesa de a UN documento (process_next) para el replay en vivo;
run_month() lo drena entero. El gate humano de liberacion del lote NO vive
aca. La aprobación para liberar dinero es siempre humana.
"""

from __future__ import annotations

from decimal import Decimal

from ..audit import AuditTrail
from ..config import DEFAULT_CONFIG, EXCEPTION_OWNERS, Controls, EngineConfig
from ..envutil import resolve_commit
from ..models import (
    ControlResult,
    Dataset,
    DOC_INVOICE,
    DOC_OTHER,
    DOC_PROFORMA,
    ExceptionItem,
    Invoice,
    InvoiceOutcome,
    PaymentBatch,
    RetencionItem,
    RunResult,
    SEVERITY_SOFT,
    STATUS_ANTICIPO_EXCEPCION,
    STATUS_ANTICIPO_PENDIENTE,
    STATUS_ANTICIPO_RETENIDO,
    STATUS_BLOQUEADA,
    STATUS_DOMICILIACION,
    STATUS_EN_LOTE,
    STATUS_OTRO_DOC,
    STATUS_PENDIENTE_DATOS_INTERNOS,
    STATUS_PROXIMO_CICLO,
    STATUS_RETENIDO_ALTA_PROVEEDOR,
    STATUS_TARJETA,
    TareaConciliacion,
)
from .controls import (
    RunContext,
    check_anticipo,
    check_autorizacion_oc,
    check_completitud,
    check_conciliacion,
    check_datos_bancarios,
    check_duplicados,
    check_gobierno_non_po,
    check_mandato_domiciliacion,
    check_match,
    check_vendor_master,
    checker_validate_imputacion,
    classify_document,
    maker_propose_gobierno_non_po,
    maker_propose_imputacion,
    maker_propose_imputacion_non_po,
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
    """Corre el mes de a un documento por vez, en orden cronologico de recepcion.

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
        self.retenciones: list[RetencionItem] = []
        self.tareas: list[TareaConciliacion] = []
        self.carryover: list[str] = []
        self._batch_map: dict = {}
        self._invoices = list(dataset.invoices)  # ya ordenadas por recepcion
        self._pos = 0
        self._finalized: RunResult | None = None
        self.audit.add(agent="orquestador", action="inicio-corrida",
                       evidence={"mes": config.demo_month, "documentos": len(self._invoices)})

    # ------------------------------------------------------------------
    @property
    def total_invoices(self) -> int:
        return len(self._invoices)

    @property
    def processed_count(self) -> int:
        return self._pos

    def process_next(self) -> tuple[Invoice, InvoiceOutcome] | None:
        """Procesa el siguiente documento; None cuando el mes esta completo."""
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

    def _retener(self, inv: Invoice, reason: str, missing: list[str],
                 propuesta: dict, detail: str, status: str,
                 results: list[ControlResult], flags: list[str]) -> InvoiceOutcome:
        """Retencion: el documento espera datos, NO esta bloqueado por control."""
        if inv.invoice_id in self.ctx.cashflow:
            self.ctx.cashflow[inv.invoice_id]["estado"] = "retenida"
        self.retenciones.append(RetencionItem(
            invoice_id=inv.invoice_id, reason=reason, missing=missing,
            propuesta=propuesta, detail=detail))
        self.audit.add(agent="orquestador", action="retencion",
                       invoice_id=inv.invoice_id, result=status,
                       evidence={"motivo": reason, "faltante": missing,
                                 "propuesta_del_agente": propuesta})
        outcome = InvoiceOutcome(
            invoice_id=inv.invoice_id, status=status, blocking_control=None,
            flags=sorted(set(flags)), batch_date=None, control_results=results)
        self.outcomes[inv.invoice_id] = outcome
        return outcome

    def _bloquear(self, inv: Invoice, blocking: ControlResult,
                  results: list[ControlResult], flags: list[str]) -> InvoiceOutcome:
        if inv.invoice_id in self.ctx.cashflow:
            self.ctx.cashflow[inv.invoice_id]["estado"] = "bloqueada"
        fraud = blocking.control_id == Controls.C6_DATOS_BANCARIOS
        self.exceptions.append(ExceptionItem(
            invoice_id=inv.invoice_id, control_id=blocking.control_id,
            severity=blocking.severity,
            owner=EXCEPTION_OWNERS.get(blocking.control_id, "AP"),
            detail=blocking.detail, evidence=blocking.evidence, fraud_alert=fraud))
        self.audit.add(agent="orquestador", action="a-cola-de-excepciones",
                       invoice_id=inv.invoice_id, control_id=blocking.control_id,
                       result="bloqueada",
                       evidence={"dueno_sugerido": EXCEPTION_OWNERS.get(blocking.control_id, "AP"),
                                 "alerta_fraude": fraud})
        outcome = InvoiceOutcome(
            invoice_id=inv.invoice_id, status=STATUS_BLOQUEADA,
            blocking_control=blocking.control_id, flags=sorted(set(flags)),
            batch_date=None, control_results=results)
        self.outcomes[inv.invoice_id] = outcome
        return outcome

    # ------------------------------------------------------------------
    def _process_proforma(self, inv: Invoice) -> InvoiceOutcome:
        """Flujo propio de anticipos: nunca toca el flujo de facturas ni lotes."""
        results: list[ControlResult] = []
        self.audit.add(agent="orquestador", action="flujo-anticipos",
                       invoice_id=inv.invoice_id,
                       evidence={"proveedor": inv.vendor_name,
                                 "importe": str(inv.amount_total)})
        if not inv.presupuesto_aprobado:
            return self._retener(
                inv, reason="aprobacion_presupuesto",
                missing=["aprobacion interna del presupuesto"],
                propuesta={}, detail="Anticipo sin presupuesto aprobado internamente",
                status=STATUS_ANTICIPO_RETENIDO, results=results, flags=[])

        res = check_anticipo(inv, self.ctx)
        results.append(res)
        self._audit_control(inv, res)
        if not res.passed:
            # excepcion del flujo de anticipos (dinero salido sin factura final)
            self.exceptions.append(ExceptionItem(
                invoice_id=inv.invoice_id,
                control_id=Controls.C8_ANTICIPO_SIN_FACTURA_FINAL,
                severity=res.severity,
                owner=EXCEPTION_OWNERS[Controls.C8_ANTICIPO_SIN_FACTURA_FINAL],
                detail=res.detail, evidence=res.evidence))
            self.audit.add(agent="orquestador", action="a-cola-de-excepciones",
                           invoice_id=inv.invoice_id,
                           control_id=Controls.C8_ANTICIPO_SIN_FACTURA_FINAL,
                           result=STATUS_ANTICIPO_EXCEPCION,
                           evidence={"dueno_sugerido":
                                     EXCEPTION_OWNERS[Controls.C8_ANTICIPO_SIN_FACTURA_FINAL]})
            status = STATUS_ANTICIPO_EXCEPCION
        else:
            status = STATUS_ANTICIPO_PENDIENTE
            self.audit.add(agent="orquestador", action="anticipo-pendiente-factura-final",
                           invoice_id=inv.invoice_id, result=status,
                           evidence={"presupuesto_aprobado": True})
        outcome = InvoiceOutcome(
            invoice_id=inv.invoice_id, status=status, blocking_control=None,
            flags=[], batch_date=None, control_results=results)
        self.outcomes[inv.invoice_id] = outcome
        return outcome

    # ------------------------------------------------------------------
    def _process(self, inv: Invoice) -> InvoiceOutcome:
        ctx, audit, dataset = self.ctx, self.audit, self.dataset
        results: list[ControlResult] = []
        flags: list[str] = []
        blocking: ControlResult | None = None

        audit.add(agent="maker-ingesta", action="intake-email",
                  invoice_id=inv.invoice_id,
                  evidence={"proveedor": inv.vendor_name, "numero": inv.invoice_number,
                            "importe": str(inv.amount_total), "moneda": inv.currency,
                            "recibida": inv.received_date.isoformat(),
                            "metodo_pago": inv.metodo_pago})

        # --- C0 clasificacion del documento (etapa 0) ---
        kind, ev = classify_document(inv)
        audit.add(agent="maker-clasificador", action="clasificacion-documento",
                  invoice_id=inv.invoice_id, control_id=Controls.C0_CLASIFICACION,
                  result=kind, evidence=ev)
        if kind == DOC_PROFORMA:
            return self._process_proforma(inv)
        if kind == DOC_OTHER:
            return self._retener(
                inv, reason="revision_manual",
                missing=["clasificacion manual del documento"],
                propuesta={}, detail="Documento con seniales mixtas (ni factura ni proforma clara)",
                status=STATUS_OTRO_DOC, results=results, flags=flags)

        # --- C1 completitud (hard) ---
        res = check_completitud(inv, ctx)
        results.append(res); self._audit_control(inv, res)
        if not res.passed:
            blocking = res
        else:
            # --- C2 duplicados (hard) contra la historia de FACTURAS ---
            res = check_duplicados(inv, ctx)
            results.append(res); self._audit_control(inv, res)
            if not res.passed:
                blocking = res

        if blocking is None:
            # Registro operativo (cashflow). Si habia carga manual heredada, se
            # respeta tal cual: la divergencia la detecta C7, no la esconde.
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

            # --- C9 completitud del maestro de proveedores (retencion) ---
            res = check_vendor_master(inv, ctx)
            results.append(res); self._audit_control(inv, res)
            if not res.passed:
                ctx.ingested.append(inv)
                return self._retener(
                    inv, reason="alta_proveedor",
                    missing=res.evidence["faltante"], propuesta={},
                    detail=res.detail, status=STATUS_RETENIDO_ALTA_PROVEEDOR,
                    results=results, flags=flags)

        imputacion_proposal: dict | None = None
        if blocking is None:
            if inv.po_ref is not None:
                # ---------- ruta PO: como hoy ----------
                res = check_autorizacion_oc(inv, ctx)
                results.append(res); self._audit_control(inv, res)
                if not res.passed:
                    blocking = res
                if blocking is None:
                    po = dataset.pos[inv.po_ref]
                    imputacion_proposal = maker_propose_imputacion(inv, po)
                    audit.add(agent="maker-imputacion", action="propuesta-imputacion",
                              invoice_id=inv.invoice_id, evidence=imputacion_proposal)
                    res = checker_validate_imputacion(inv, imputacion_proposal, ctx)
                    results.append(res); self._audit_control(inv, res)
                    if not res.passed:
                        flags.append(FLAG_BY_CONTROL_SOFT[Controls.C4_IMPUTACION])
                    if res.evidence.get("clasificacion") == "intercompany":
                        flags.append("INTERCOMPANY")
                    res = check_match(inv, ctx)
                    results.append(res); self._audit_control(inv, res)
                    if not res.passed:
                        if res.severity == SEVERITY_SOFT:
                            flags.append(FLAG_BY_CONTROL_SOFT[Controls.C5_MATCH])
                        else:
                            blocking = res
            else:
                # ---------- ruta non-PO gobernada ----------
                propuesta = maker_propose_gobierno_non_po(inv, ctx)
                audit.add(agent="maker-gobierno-non-po", action="propuesta-datos-internos",
                          invoice_id=inv.invoice_id, evidence=propuesta)
                res = check_gobierno_non_po(inv, ctx)
                results.append(res); self._audit_control(inv, res)
                if not res.passed:
                    ctx.ingested.append(inv)
                    return self._retener(
                        inv, reason="datos_internos",
                        missing=res.evidence["faltante"], propuesta=propuesta,
                        detail=res.detail, status=STATUS_PENDIENTE_DATOS_INTERNOS,
                        results=results, flags=flags)
                imputacion_proposal = maker_propose_imputacion_non_po(inv, ctx)
                audit.add(agent="maker-imputacion", action="propuesta-imputacion",
                          invoice_id=inv.invoice_id, evidence=imputacion_proposal)
                res = checker_validate_imputacion(inv, imputacion_proposal, ctx)
                results.append(res); self._audit_control(inv, res)
                if not res.passed:
                    flags.append(FLAG_BY_CONTROL_SOFT[Controls.C4_IMPUTACION])
                if res.evidence.get("clasificacion") == "intercompany":
                    flags.append("INTERCOMPANY")

        if blocking is None and inv.metodo_pago == "transferencia":
            # --- C6 datos bancarios vs maestro (SOLO transferencias) ---
            res = check_datos_bancarios(inv, ctx)
            results.append(res); self._audit_control(inv, res)
            if not res.passed:
                blocking = res

        if blocking is None and inv.metodo_pago == "domiciliacion_direct_debit":
            # --- C11 mandato SEPA registrado ---
            res = check_mandato_domiciliacion(inv, ctx)
            results.append(res); self._audit_control(inv, res)
            if not res.passed:
                blocking = res

        if blocking is None:
            # Asiento propuesto en el ERP simulado, con tratamiento de IVA
            gl = imputacion_proposal["gl_account"] if imputacion_proposal else None
            ctx.erp[inv.invoice_id] = {
                "amount": inv.amount_total, "status": "contabilizada", "matched": True,
                "gl_account": gl,
                "tratamiento_iva": inv.tratamiento_iva,
            }
            audit.add(agent="maker-contable", action="contabilizacion-erp",
                      invoice_id=inv.invoice_id,
                      evidence={"importe": str(inv.amount_total), "cuenta": gl,
                                "tratamiento_iva": inv.tratamiento_iva})

            # --- C7 conciliacion pre-pago cashflow vs ERP (hard) ---
            res = check_conciliacion(inv, ctx)
            results.append(res); self._audit_control(inv, res)
            if not res.passed:
                blocking = res

        # ---- resolucion del documento ----
        if blocking is not None:
            outcome = self._bloquear(inv, blocking, results, flags)
        else:
            # Consumo de OC solo para facturas totalmente limpias de hard
            if inv.po_ref:
                ctx.po_consumed[inv.po_ref] = (
                    ctx.po_consumed.get(inv.po_ref, Decimal("0")) + inv.amount_total)

            if inv.metodo_pago == "domiciliacion_direct_debit":
                ctx.cashflow[inv.invoice_id]["estado"] = "domiciliacion en curso"
                self.tareas.append(TareaConciliacion(
                    invoice_id=inv.invoice_id, tipo="post_debito",
                    detail="Conciliar el cargo bancario del debito contra el asiento"))
                audit.add(agent="orquestador", action="tarea-conciliacion",
                          invoice_id=inv.invoice_id, result=STATUS_DOMICILIACION,
                          evidence={"tipo": "post_debito",
                                    "mandato": dataset.vendors[inv.vendor_id].sepa_mandate_ref})
                status, bdate = STATUS_DOMICILIACION, None
            elif inv.metodo_pago == "tarjeta":
                ctx.cashflow[inv.invoice_id]["estado"] = "pagada con tarjeta (a conciliar)"
                self.tareas.append(TareaConciliacion(
                    invoice_id=inv.invoice_id, tipo="extracto_tarjeta",
                    detail="Conciliar contra el extracto mensual de la tarjeta"))
                audit.add(agent="orquestador", action="tarea-conciliacion",
                          invoice_id=inv.invoice_id, result=STATUS_TARJETA,
                          evidence={"tipo": "extracto_tarjeta"})
                status, bdate = STATUS_TARJETA, None
            else:
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
                batch_date=bdate, control_results=results)
            self.outcomes[inv.invoice_id] = outcome

        # Toda FACTURA ingresada (pase o no) queda en la historia para
        # duplicados. Proformas y "other" no: compararlas contra la factura
        # final legitima daria falsos casi-duplicados.
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
                                 "retenciones": len(self.retenciones),
                                 "tareas_conciliacion": len(self.tareas),
                                 "proximo_ciclo": len(self.carryover)})
        self._finalized = RunResult(
            run_id=self.audit.run_id, commit=self.audit.commit, outcomes=self.outcomes,
            batches=batches, exceptions=self.exceptions, carryover_ids=self.carryover,
            retenciones=self.retenciones, tareas=self.tareas,
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
