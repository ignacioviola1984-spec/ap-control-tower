"""Vista 5: Registro de auditoria.

Tabla cronologica completa de la corrida, filtrable por factura / agente /
control, con export a CSV. Cadena de hashes verificable en vivo.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from ..state import get_run, run_is_ready
from ..theme import badge


def render() -> None:
    st.markdown("## Registro de auditoría")
    if not run_is_ready():
        st.info("Corré el mes primero (vista **Corrida del mes**).")
        return
    run = get_run()
    audit = run["audit"]

    chain_ok = audit.verify_chain()
    st.markdown(
        f"<div class='apct-card'>Cada evento registra agente, acción, factura, "
        f"control, resultado, evidencia, timestamp, run_id y commit; el hash de "
        f"cada evento encadena al anterior. &nbsp;"
        f"{badge('CADENA VERIFICADA', 'ok') if chain_ok else badge('CADENA ROTA', 'block')} "
        f"&nbsp; {len(audit.events)} eventos · corrida <code>{audit.run_id}</code> · "
        f"commit <code>{audit.commit}</code></div>",
        unsafe_allow_html=True,
    )

    df = pd.DataFrame([{
        "#": ev.seq,
        "timestamp": ev.ts,
        "agente": ev.agent,
        "acción": ev.action,
        "factura": ev.invoice_id or "",
        "control": ev.control_id or "",
        "resultado": ev.result or "",
        "evidencia": json.dumps(ev.evidence, ensure_ascii=False, default=str),
        "hash": ev.hash[:12],
    } for ev in audit.events])

    c1, c2, c3, c4 = st.columns([1, 1, 1, 1.4])
    f_inv = c1.selectbox("Factura", ["(todas)"] + sorted(x for x in df["factura"].unique() if x))
    f_agent = c2.selectbox("Agente", ["(todos)"] + sorted(df["agente"].unique()))
    f_ctrl = c3.selectbox("Control", ["(todos)"] + sorted(x for x in df["control"].unique() if x))
    f_text = c4.text_input("Buscar en evidencia", placeholder="p. ej. IBAN, 1476.30, aprobador")

    view = df
    if f_inv != "(todas)":
        view = view[view["factura"] == f_inv]
    if f_agent != "(todos)":
        view = view[view["agente"] == f_agent]
    if f_ctrl != "(todos)":
        view = view[view["control"] == f_ctrl]
    if f_text:
        view = view[view["evidencia"].str.contains(f_text, case=False, regex=False)]

    st.dataframe(view, height=520, hide_index=True,
                 column_config={"evidencia": st.column_config.TextColumn(width="large")})
    st.download_button(
        "⬇ Exportar corrida completa a CSV",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"audit_{audit.run_id}.csv",
        mime="text/csv",
    )
