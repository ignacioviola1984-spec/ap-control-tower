"""Diagnóstico opcional del asistente para una instancia administrativa separada."""

from __future__ import annotations

import hmac
import os

import pandas as pd
import streamlit as st

from .pilot_pages_common import metric_row, page_header
from .trial import session as sess


def _authorized() -> bool:
    expected = os.environ.get("AP_AGENT_ADMIN_PASSWORD", "").strip()
    if not expected:
        st.error("El acceso administrativo no está configurado.")
        return False
    if st.session_state.get("_agent_admin_ok"):
        return True
    with st.form("agent_admin_login", border=True):
        password = st.text_input("Contraseña administrativa", type="password")
        submitted = st.form_submit_button(
            "Ingresar", type="primary", icon=":material/login:"
        )
    if submitted and hmac.compare_digest(
        (password or "").encode("utf-8"), expected.encode("utf-8")
    ):
        st.session_state["_agent_admin_ok"] = True
        st.rerun()
    elif submitted:
        st.error("La contraseña ingresada es incorrecta.")
    return False


def render_agent_admin() -> None:
    page_header(
        "Operación del asistente",
        "Metadatos técnicos sin prompts, respuestas ni valores documentales.",
    )
    if not _authorized():
        st.stop()
    active = sess.get_session()
    events = [
        event
        for event in active.audit.events
        if event.action == "consulta-asistente-ap"
    ]
    answered = [event for event in events if event.result == "respondida"]
    total_tokens = sum(
        int(event.evidence.get("input_tokens", 0))
        + int(event.evidence.get("output_tokens", 0))
        for event in answered
    )
    average_latency = (
        round(
            sum(int(event.evidence.get("latencia_ms", 0)) for event in answered)
            / len(answered)
        )
        if answered
        else 0
    )
    metric_row(
        [
            ("Consultas", len(events)),
            ("Respondidas", len(answered)),
            ("Errores", len(events) - len(answered)),
            ("Tokens", total_tokens),
            ("Latencia promedio", f"{average_latency:,} ms"),
        ]
    )
    if not events:
        st.info("Todavía no hay consultas registradas en esta sesión.")
        return
    frame = pd.DataFrame(
        [
            {
                "Fecha": event.ts,
                "Documento": event.invoice_id,
                "Resultado": event.result,
                "Modelo": event.evidence.get("modelo") or "—",
                "Tools": ", ".join(event.evidence.get("tools") or []),
                "Tokens": int(event.evidence.get("input_tokens", 0))
                + int(event.evidence.get("output_tokens", 0)),
                "Latencia (ms)": event.evidence.get("latencia_ms") or "—",
            }
            for event in reversed(events)
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")
