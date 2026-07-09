"""Vista 5: Registro de auditoria.

Tabla cronologica completa de la corrida, filtrable por factura / agente /
control, con export a CSV (corrida completa) y a PDF (respeta los filtros
activos). El PDF se genera 100% local con reportlab: sin red.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..state import get_run, run_is_ready
from ..theme import badge

PDF_PRIMARY = colors.HexColor("#0F4C81")
PDF_GREY = colors.HexColor("#5A6572")


def _pdf_bytes(view: pd.DataFrame, total_events: int, audit, filters_desc: str) -> bytes:
    """PDF legible del registro: encabezado con run_id/commit/timestamp,
    tabla cronologica (lo filtrado) y pie con la verificacion de cadena."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title="AP Control Tower - Registro de auditoria",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=15,
                        textColor=PDF_PRIMARY, alignment=0, spaceAfter=2)
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=8.5,
                          textColor=PDF_GREY, spaceAfter=1)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7, leading=8.6)
    cell_g = ParagraphStyle("cellg", parent=cell, textColor=PDF_GREY)

    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    story = [
        Paragraph("AP Control Tower · Registro de auditoría", h1),
        Paragraph(f"Corrida <b>{audit.run_id}</b> · commit <b>{audit.commit}</b> · "
                  f"PDF generado {generated} (UTC)", meta),
        Paragraph(f"Eventos exportados: <b>{len(view)}</b> de {total_events} · "
                  f"Filtros: {filters_desc}", meta),
        Spacer(1, 4 * mm),
    ]

    head = ["#", "Timestamp", "Agente", "Acción", "Factura", "Control",
            "Resultado", "Evidencia"]
    data = [[Paragraph(f"<b>{h}</b>",
                       ParagraphStyle("th", parent=cell, textColor=colors.white))
             for h in head]]
    for _, r in view.iterrows():
        ev = r["evidencia"]
        if len(ev) > 220:
            ev = ev[:220] + "…"
        data.append([
            Paragraph(str(r["#"]), cell),
            Paragraph(str(r["timestamp"]), cell_g),
            Paragraph(str(r["agente"]), cell),
            Paragraph(str(r["acción"]), cell),
            Paragraph(str(r["factura"]), cell),
            Paragraph(str(r["control"]), cell),
            Paragraph(str(r["resultado"]), cell),
            Paragraph(str(ev).replace("<", "&lt;").replace(">", "&gt;"), cell_g),
        ])
    table = Table(
        data, repeatRows=1,
        colWidths=[9 * mm, 34 * mm, 30 * mm, 34 * mm, 16 * mm, 30 * mm, 18 * mm, 102 * mm],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PDF_PRIMARY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F5F9")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D5DCE5")),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(table)

    chain_ok = audit.verify_chain()
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        f"Verificación de cadena de hashes: "
        f"<b>{'VERIFICADA' if chain_ok else 'ROTA'}</b> sobre los {total_events} "
        f"eventos de la corrida. Cada evento encadena el hash del anterior: alterar "
        f"uno rompe la cadena. Datos 100% sintéticos (modo demo).",
        ParagraphStyle("foot", parent=meta,
                       textColor=(PDF_GREY if chain_ok else colors.red)),
    ))
    doc.build(story)
    return buf.getvalue()


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
    active_filters = []
    if f_inv != "(todas)":
        view = view[view["factura"] == f_inv]
        active_filters.append(f"factura={f_inv}")
    if f_agent != "(todos)":
        view = view[view["agente"] == f_agent]
        active_filters.append(f"agente={f_agent}")
    if f_ctrl != "(todos)":
        view = view[view["control"] == f_ctrl]
        active_filters.append(f"control={f_ctrl}")
    if f_text:
        view = view[view["evidencia"].str.contains(f_text, case=False, regex=False)]
        active_filters.append(f"texto contiene '{f_text}'")
    filters_desc = "; ".join(active_filters) if active_filters else "ninguno (corrida completa)"

    st.dataframe(view, height=520, hide_index=True,
                 column_config={"evidencia": st.column_config.TextColumn(width="large")})

    col_csv, col_pdf = st.columns(2)
    col_csv.download_button(
        "⬇ Exportar corrida completa a CSV",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"audit_{audit.run_id}.csv",
        mime="text/csv",
        use_container_width=True,
    )
    col_pdf.download_button(
        "⬇ Exportar a PDF",
        _pdf_bytes(view, len(df), audit, filters_desc),
        file_name=f"audit_{audit.run_id}.pdf",
        mime="application/pdf",
        use_container_width=True,
        help="Respeta los filtros activos: exporta lo que estás viendo.",
    )
