"""Binding Streamlit <-> capa de aplicacion.

Fina por diseno (Fase 3): la orquestacion de negocio vive en
``ap_control_tower.app``; este modulo solo guarda el estado de corrida en
session_state y delega. La UI no importa engine/ ni contiene reglas centrales.
El estado de corrida es el mismo dict de siempre (compatibilidad):
    {"result", "audit", "ctx", "workflows", "closing_reports"}.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from .. import app
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
    """Guarda la corrida (con los dos sign-offs agenticos ya corridos por la
    capa de aplicacion), pendiente SOLO de la aprobacion humana (el gate)."""
    st.session_state["run"] = app.build_run(result, audit, ctx)


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


# ------------------------------------------------ acciones (delegan a app/)
def confirm_internal_data_action(invoice_id: str, confirmed_by: str,
                                 cost_center: str, internal_approver: str,
                                 contract_ref: str) -> str:
    """Confirmacion humana de datos internos: cambia el estado real y, si la
    factura entra a un lote, ese lote se reabre (checkers de nuevo + gate)."""
    return app.confirm_internal_data(
        get_dataset(), get_run(), confirmed_by=confirmed_by, invoice_id=invoice_id,
        cost_center=cost_center, internal_approver=internal_approver,
        contract_ref=contract_ref)


def approve_anticipo_action(invoice_id: str, confirmed_by: str) -> str:
    return app.approve_anticipo(get_dataset(), get_run(),
                                confirmed_by=confirmed_by, invoice_id=invoice_id)


def approve_and_release_action(batch_iso: str, approver: str):
    """El gate humano: aprueba y libera un lote al banco."""
    return app.approve_and_release(get_run(), batch_iso, approver)


def reject_batch_action(batch_iso: str, approver: str, reason: str):
    return app.reject_batch(get_run(), batch_iso, approver, reason)


def close_batch_action(batch_iso: str):
    """Cierre contable del lote liberado (conciliacion pago vs pasivo)."""
    return app.close_batch(get_run(), batch_iso)
