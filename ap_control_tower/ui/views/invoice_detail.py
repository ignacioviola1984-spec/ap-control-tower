"""Vista 2: Detalle de factura (documento -> datos).

El documento sintetico renderizado al lado de los datos extraidos y el
resultado de cada control aplicado. Un CFO no tecnico tiene que entender
que hace el agente en 10 segundos.
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from ...models import STATUS_BLOQUEADA
from ..state import doc_preview_html, get_dataset, get_run, run_is_ready
from ..theme import badge, eur, status_badge

KEY_INVOICES = ["INV-024", "INV-023", "INV-014", "INV-025", "INV-009", "INV-005"]
KEY_LABELS = {
    "INV-024": "INV-024 · EL FRAUDE (IBAN cambiado)",
    "INV-023": "INV-023 · duplicada exacta",
    "INV-014": "INV-014 · email sin OC",
    "INV-025": "INV-025 · match fuera de tolerancia",
    "INV-009": "INV-009 · diferencia menor (soft)",
    "INV-005": "INV-005 · factura limpia",
}


def _control_row(res) -> str:
    if res.passed:
        verdict = badge("PASA", "ok")
    elif res.severity == "hard":
        verdict = badge("BLOQUEA", "block")
    else:
        verdict = badge("FLAG", "flag")
    sev = badge(res.severity.upper(), "mut")
    return (f"<tr><td><b>{res.control_id}</b><br>"
            f"<span style='color:#5A6572;font-size:12px;'>{res.control_name}</span></td>"
            f"<td>{sev}</td><td>{verdict}</td><td>{res.detail}</td>"
            f"<td style='color:#5A6572;font-size:12px;'>{res.checker}</td></tr>")


def render() -> None:
    st.markdown("## Detalle de factura: del documento a los datos")
    if not run_is_ready():
        st.info("Corré el mes primero (vista **Corrida del mes**) para ver los "
                "controles aplicados a cada factura.")
        return
    ds = get_dataset()
    run = get_run()
    result = run["result"]
    invoices = {i.invoice_id: i for i in ds.invoices}

    all_ids = KEY_INVOICES + [i.invoice_id for i in ds.invoices
                              if i.invoice_id not in KEY_INVOICES]
    chosen = st.selectbox(
        "Factura", all_ids,
        format_func=lambda x: KEY_LABELS.get(x, f"{x} · {invoices[x].vendor_name}"),
    )
    inv = invoices[chosen]
    o = result.outcomes[chosen]
    vendor = ds.vendors[inv.vendor_id]
    po = ds.pos.get(inv.po_ref) if inv.po_ref else None

    left, right = st.columns([1.05, 1.15], gap="large")
    with left:
        st.markdown("##### El documento que llegó por email")
        html = doc_preview_html(chosen)
        if html:
            components.html(html, height=760, scrolling=True)
        else:
            st.markdown(
                "<div class='apct-card' style='color:#5A6572;'>Sin render visual para "
                "esta factura (los 6 casos clave del guión tienen documento). Los datos "
                "estructurados están a la derecha.</div>", unsafe_allow_html=True)

    with right:
        st.markdown("##### Lo que el agente extrajo y decidió")
        st.markdown(
            f"<div class='apct-card'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
            f"<div style='font-size:17px;font-weight:750;'>{inv.invoice_id} · "
            f"{inv.vendor_name}</div>{status_badge(o.status)}</div>"
            f"<table class='apct-table' style='margin-top:10px;border:none;'>"
            f"<tr><td>Número</td><td><b>{inv.invoice_number}</b></td>"
            f"<td>Emisión / recepción</td><td>{inv.issue_date} / {inv.received_date}</td></tr>"
            f"<tr><td>Importe</td><td><b>{eur(inv.amount_total)} {inv.currency}</b></td>"
            f"<td>OC referenciada</td><td>{inv.po_ref or '—'}"
            f"{(' · línea ' + inv.po_line_ref) if inv.po_line_ref else ''}</td></tr>"
            f"<tr><td>Proyecto / BU</td><td>{(po.project_code + ' · ' + po.project_code[:2]) if po else '—'}</td>"
            f"<td>Cuenta contable</td><td>{po.gl_account if po else '—'}</td></tr>"
            f"<tr><td>IBAN en factura</td><td colspan='3'><code>{inv.iban_on_invoice}</code></td></tr>"
            f"<tr><td>IBAN en maestro</td><td colspan='3'><code>{vendor.iban}</code></td></tr>"
            f"</table></div>",
            unsafe_allow_html=True,
        )
        if o.status == STATUS_BLOQUEADA:
            exc = next(e for e in run["result"].exceptions if e.invoice_id == chosen)
            st.markdown(
                f"<div class='apct-card' style='border-left:4px solid #C0392B;'>"
                f"<b>Bloqueada por {exc.control_id}</b> · dueño sugerido: {exc.owner}"
                f"<br><span style='color:#5A6572;'>{exc.detail}</span></div>",
                unsafe_allow_html=True,
            )
        st.markdown("##### Controles aplicados, en orden")
        rows = "".join(_control_row(r) for r in o.control_results)
        st.markdown(
            "<table class='apct-table'><tr><th>Control</th><th>Tipo</th>"
            "<th>Resultado</th><th>Detalle</th><th>Checker</th></tr>" + rows + "</table>",
            unsafe_allow_html=True,
        )
