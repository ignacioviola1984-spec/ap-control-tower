"""Navegación del Trial: cinco vistas internas del circuito real."""

from __future__ import annotations

import streamlit as st

from ..navigation import scroll_to_top_on_change
from .step_navigation import apply_pending

INTAKE = "🧾  Probar con mis facturas"
RESULTS = "📊  Ver resultados con mis facturas"
HUMAN_REVIEW = "📋  Revisión humana"
PAYMENT_APPROVAL = "✅  Aprobación para propuesta de pago"
BUSINESS_CASE = "📈  Consultar caso de negocio"

TRIAL_OPTIONS = [INTAKE, RESULTS, HUMAN_REVIEW, PAYMENT_APPROVAL, BUSINESS_CASE]


def render() -> None:
    from . import (business_case, human_review, intake, payment_approval,
                   results, session)

    apply_pending(TRIAL_OPTIONS)
    session.render_sidebar_actions()

    choice = st.sidebar.radio(
        "Navegación", TRIAL_OPTIONS, label_visibility="collapsed",
        key="_trial_navigation")
    session.render_sidebar_end_session()
    if choice == INTAKE:
        intake.render()
    elif choice == RESULTS:
        results.render()
    elif choice == HUMAN_REVIEW:
        human_review.render()
    elif choice == PAYMENT_APPROVAL:
        payment_approval.render()
    else:
        business_case.render()
    scroll_to_top_on_change(choice, state_key="_trial_last_view")
