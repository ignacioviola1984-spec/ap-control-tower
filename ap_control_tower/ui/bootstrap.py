"""Arranque único de la Torre de Control para Cuentas a Pagar."""

from __future__ import annotations

import streamlit as st

PRODUCT_MODE = "product"
PRODUCT_TITLE = "Torre de Control para Cuentas a Pagar"


def normalize_mode(mode: str | None) -> str:
    """Shim: cualquier modo histórico conduce al único producto operativo."""
    del mode
    return PRODUCT_MODE


def run(mode: str | None = None) -> None:
    normalize_mode(mode)
    st.set_page_config(
        page_title=PRODUCT_TITLE,
        page_icon=":material/account_balance_wallet:",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    from .auth import require_password

    require_password()

    from .pilot_shell import render

    render()
