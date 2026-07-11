"""Capa de aplicacion / casos de uso (Fase 3).

Concentra la ORQUESTACION de negocio que antes vivia en ui/state.py y en las
vistas. Es framework-agnostica: NO importa Streamlit ni toca session_state. La
UI (o una futura API) invoca estas funciones; el motor (engine/) sigue siendo
la unica fuente de las reglas. La persistencia es opcional y se enchufa en
fases posteriores sin cambiar esta interfaz.

El "estado de corrida" es el mismo dict que usa la UI hoy (compatibilidad):
    {"result", "audit", "ctx", "workflows", "closing_reports"}
Las mutaciones del gate/revision operan ese dict in situ, igual que hoy.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..config import DEFAULT_CONFIG, EngineConfig
from ..engine.batch import (
    ESTADO_PENDIENTE_HUMANO,
    ESTADO_PROPUESTO,
    ESTADO_REVALIDADO_A,
    BatchWorkflow,
)
from ..engine.closing import close_batch as _close_batch
from ..engine.pipeline import MonthRunner, run_month
from ..engine.review import approve_anticipo as _approve_anticipo
from ..engine.review import confirm_internal_data as _confirm_internal_data
from ..models import Dataset

RunState = dict[str, Any]


# ------------------------------------------------------------------ corrida
def new_month_runner(dataset: Dataset, config: EngineConfig = DEFAULT_CONFIG,
                     run_id: str | None = None) -> MonthRunner:
    """Runner incremental para el replay en vivo de la UI (process_next)."""
    return MonthRunner(dataset, config=config, run_id=run_id)


def _build_workflows(result, ctx, audit, config: EngineConfig) -> dict:
    """Cada lote queda con sus dos sign-offs agenticos corridos, pendiente SOLO
    de la aprobacion humana (el gate)."""
    workflows: dict[str, BatchWorkflow] = {}
    for b in result.batches:
        wf = BatchWorkflow(b, result, ctx, audit, config)
        a = wf.run_checker_a()
        if a.ok:
            wf.run_checker_b()
        workflows[b.batch_date.isoformat()] = wf
    return workflows


def build_run(result, audit, ctx, config: EngineConfig = DEFAULT_CONFIG) -> RunState:
    """Arma el estado de corrida a partir de un RunResult ya producido."""
    return {"result": result, "audit": audit, "ctx": ctx,
            "workflows": _build_workflows(result, ctx, audit, config),
            "closing_reports": {}}


def process_month(dataset: Dataset, config: EngineConfig = DEFAULT_CONFIG,
                  run_id: str | None = None) -> RunState:
    """Corre el mes completo y arma el estado de corrida (con checkers)."""
    result, audit, ctx = run_month(dataset, config=config, run_id=run_id)
    return build_run(result, audit, ctx, config)


def finalize_runner(runner: MonthRunner, config: EngineConfig = DEFAULT_CONFIG) -> RunState:
    """Cierra un runner drenado (tras el replay en vivo) y arma la corrida."""
    result = runner.finalize()
    return build_run(result, runner.audit, runner.ctx, config)


# ------------------------------------------------------------------ lotes abiertos
def assignable_thursdays(run: RunState) -> list[date]:
    """Jueves cuyos lotes admiten incorporaciones (aun no aprobados/liberados/
    rechazados/cerrados)."""
    abiertos: list[date] = []
    for iso, wf in run["workflows"].items():
        if iso in run["closing_reports"]:
            continue
        if wf.state in (ESTADO_PROPUESTO, ESTADO_REVALIDADO_A, ESTADO_PENDIENTE_HUMANO):
            abiertos.append(wf.batch.batch_date)
    return abiertos


def reopen_workflow(run: RunState, batch_iso: str,
                    config: EngineConfig = DEFAULT_CONFIG) -> None:
    """El lote cambio: workflow nuevo desde cero, dos sign-offs de nuevo."""
    result = run["result"]
    batch = next(b for b in result.batches if b.batch_date.isoformat() == batch_iso)
    wf = BatchWorkflow(batch, result, run["ctx"], run["audit"], config)
    a = wf.run_checker_a()
    if a.ok:
        wf.run_checker_b()
    run["workflows"][batch_iso] = wf


# ------------------------------------------------------------------ el gate
def approve_and_release(run: RunState, batch_iso: str, approver: str):
    """Aprueba y libera un lote al banco. Levanta GateViolation si el estado no
    lo permite o falta el nombre/sign-offs."""
    wf = run["workflows"][batch_iso]
    decision = wf.approve(approver)
    wf.release_to_bank()
    return decision


def reject_batch(run: RunState, batch_iso: str, approver: str, reason: str):
    """Rechaza y devuelve un lote. Levanta GateViolation si falta nombre/motivo."""
    wf = run["workflows"][batch_iso]
    return wf.reject(approver, reason)


def close_batch(run: RunState, batch_iso: str):
    """Cierra un lote liberado (conciliacion pago vs pasivo) y guarda el reporte."""
    wf = run["workflows"][batch_iso]
    report = _close_batch(wf, run["ctx"], run["audit"])
    run["closing_reports"][batch_iso] = report
    return report


# ------------------------------------------------------------------ revision humana
def confirm_internal_data(dataset: Dataset, run: RunState, *, confirmed_by: str,
                          invoice_id: str, cost_center: str, internal_approver: str,
                          contract_ref: str,
                          config: EngineConfig = DEFAULT_CONFIG) -> str:
    """Confirma datos internos de una non-PO retenida; si entra a un lote, ese
    lote se reabre (checkers de nuevo + gate). NUNCA libera un pago."""
    status = _confirm_internal_data(
        dataset, run["result"], run["ctx"], run["audit"],
        confirmed_by=confirmed_by, invoice_id=invoice_id,
        cost_center=cost_center, internal_approver=internal_approver,
        contract_ref=contract_ref, assignable_thursdays=assignable_thursdays(run),
        config=config)
    outcome = run["result"].outcomes[invoice_id]
    if outcome.batch_date is not None:
        reopen_workflow(run, outcome.batch_date.isoformat(), config)
    return status


def approve_anticipo(dataset: Dataset, run: RunState, *, confirmed_by: str,
                     invoice_id: str) -> str:
    """Aprueba el presupuesto de una proforma retenida (nunca libera pagos)."""
    return _approve_anticipo(dataset, run["result"], run["ctx"], run["audit"],
                             confirmed_by, invoice_id)
