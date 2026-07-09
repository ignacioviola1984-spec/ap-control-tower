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


def doc_preview_html(invoice_id: str) -> str | None:
    path = DOC_PREVIEWS / f"{invoice_id}.html"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None
