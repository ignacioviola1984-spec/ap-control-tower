"""Estado de sesion de la UI: dataset, corrida del motor y workflows de lote.

La corrida vive en session_state: los cambios de estado que produce el gate
humano (aprobar / rechazar / liberar / cerrar) mutan el MISMO RunResult que
ven todas las vistas -> estados consecuentes en todo el sistema.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from ..config import DEFAULT_CONFIG
from ..engine.batch import BatchWorkflow
from ..models import Dataset, load_dataset

ROOT = Path(__file__).resolve().parent.parent.parent
DATASET_PATH = ROOT / "data" / "synthetic_month.json"
DOC_PREVIEWS = ROOT / "data" / "doc_previews"


@st.cache_resource(show_spinner=False)
def get_dataset() -> Dataset:
    return load_dataset(str(DATASET_PATH))


def run_is_ready() -> bool:
    return st.session_state.get("run") is not None


def store_run(result, audit, ctx) -> None:
    """Guarda la corrida y deja cada lote con sus dos sign-offs agenticos
    corridos, pendiente SOLO de la aprobacion humana (el gate)."""
    workflows: dict[str, BatchWorkflow] = {}
    for b in result.batches:
        wf = BatchWorkflow(b, result, ctx, audit, DEFAULT_CONFIG)
        a = wf.run_checker_a()
        if a.ok:
            wf.run_checker_b()
        workflows[b.batch_date.isoformat()] = wf
    st.session_state["run"] = {
        "result": result, "audit": audit, "ctx": ctx,
        "workflows": workflows, "closing_reports": {},
    }


def get_run() -> dict | None:
    return st.session_state.get("run")


def reset_run() -> None:
    st.session_state["run"] = None
    # Las confirmaciones humanas mutan el dataset en memoria (datos internos
    # confirmados): reprocesar desde cero exige recargarlo desde disco.
    get_dataset.clear()


def doc_preview_html(invoice_id: str) -> str | None:
    path = DOC_PREVIEWS / f"{invoice_id}.html"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


# ------------------------------------------------ acciones de revision humana
def _assignable_thursdays(run: dict) -> list:
    """Jueves cuyos lotes admiten incorporaciones: aun no aprobados/liberados/
    rechazados ni cerrados. Un lote que cambia pierde sus sign-offs."""
    from ..engine.batch import ESTADO_PENDIENTE_HUMANO, ESTADO_PROPUESTO, ESTADO_REVALIDADO_A

    abiertos = []
    for iso, wf in run["workflows"].items():
        if iso in run["closing_reports"]:
            continue
        if wf.state in (ESTADO_PROPUESTO, ESTADO_REVALIDADO_A, ESTADO_PENDIENTE_HUMANO):
            abiertos.append(wf.batch.batch_date)
    return abiertos


def _reopen_workflow(run: dict, batch_date_iso: str) -> None:
    """El lote cambio: workflow nuevo desde cero, dos sign-offs de nuevo."""
    from ..engine.batch import BatchWorkflow

    result = run["result"]
    batch = next(b for b in result.batches if b.batch_date.isoformat() == batch_date_iso)
    wf = BatchWorkflow(batch, result, run["ctx"], run["audit"], DEFAULT_CONFIG)
    a = wf.run_checker_a()
    if a.ok:
        wf.run_checker_b()
    run["workflows"][batch_date_iso] = wf


def confirm_internal_data_action(invoice_id: str, confirmed_by: str,
                                 cost_center: str, internal_approver: str,
                                 contract_ref: str) -> str:
    """Confirmacion humana de datos internos: cambia el estado real y, si la
    factura entra a un lote, ese lote se reabre (checkers de nuevo + gate)."""
    from ..engine.review import confirm_internal_data

    run = get_run()
    status = confirm_internal_data(
        get_dataset(), run["result"], run["ctx"], run["audit"],
        confirmed_by=confirmed_by, invoice_id=invoice_id,
        cost_center=cost_center, internal_approver=internal_approver,
        contract_ref=contract_ref,
        assignable_thursdays=_assignable_thursdays(run),
    )
    outcome = run["result"].outcomes[invoice_id]
    if outcome.batch_date is not None:
        _reopen_workflow(run, outcome.batch_date.isoformat())
    return status


def approve_anticipo_action(invoice_id: str, confirmed_by: str) -> str:
    from ..engine.review import approve_anticipo

    run = get_run()
    return approve_anticipo(get_dataset(), run["result"], run["ctx"],
                            run["audit"], confirmed_by, invoice_id)
