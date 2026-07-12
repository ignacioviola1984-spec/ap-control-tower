"""Acceso externo desde la Demo a la prueba con facturas reales."""

from __future__ import annotations

from html import escape
import os

import streamlit as st

POC_URL_ENV = "AP_POC_URL"


def poc_url() -> str | None:
    """Devuelve la URL del PoC configurada fuera del código."""
    url = os.environ.get(POC_URL_ENV)
    return url.strip() if url and url.strip() else None


def render_sidebar() -> None:
    """Muestra una tarjeta separada que abre el PoC en otra pestaña."""
    url = poc_url()
    if not url:
        return

    safe_url = escape(url, quote=True)
    st.sidebar.markdown("---")
    st.sidebar.caption("Prueba de concepto con facturas reales")
    st.sidebar.html(
        "<a href='" + safe_url + "' target='_blank' rel='noopener noreferrer' "
        "style='display:block;width:100%;box-sizing:border-box;padding:10px 12px;"
        "border-radius:8px;background:#0F4C81;border:1px solid #2E6FA7;"
        "color:#FFFFFF !important;text-decoration:none !important;text-align:center;"
        "font-weight:700;font-size:14px;'>↗&nbsp; Abrir prueba con facturas reales</a>"
    )
