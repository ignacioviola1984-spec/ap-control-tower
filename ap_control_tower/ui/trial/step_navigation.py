"""Navegación segura entre pasos del Trial.

Streamlit no permite modificar el estado de un widget después de instanciarlo.
Los botones guardan un destino pendiente y el shell lo aplica en el rerun,
antes de crear el selector lateral.
"""

from __future__ import annotations

import streamlit as st

PENDING_KEY = "_trial_navigation_pending"
NAVIGATION_KEY = "_trial_navigation"


def resolve_pending(state, valid_options: list[str]) -> str | None:
    pending = state.pop(PENDING_KEY, None)
    if pending in valid_options:
        state[NAVIGATION_KEY] = pending
        return pending
    return None


def apply_pending(valid_options: list[str]) -> None:
    resolve_pending(st.session_state, valid_options)


def request_navigation(target: str) -> None:
    st.session_state[PENDING_KEY] = target
    st.rerun()


def render_next(label: str, target: str, *, key: str) -> None:
    st.markdown("---")
    if st.button(label, type="primary", use_container_width=True, key=key):
        request_navigation(target)
