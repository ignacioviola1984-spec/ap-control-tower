"""Vista 3: Cola de excepciones.

Las bloqueadas con el control que las freno, la evidencia (esperado vs
recibido) y el dueno sugerido. El fraude bancario tiene pantalla de alerta
dedicada con el diff contra el maestro de proveedores.
"""

from __future__ import annotations

import streamlit as st

from ..state import get_dataset, get_run, run_is_ready
from ..theme import badge, eur


def _fraud_screen(exc, inv, vendor) -> None:
    st.markdown(
        f"<div class='apct-fraud'>"
        f"<h3>⚠ ALERTA DE POSIBLE FRAUDE · SEVERIDAD MÁXIMA</h3>"
        f"<div style='font-size:14px;'>La cuenta destino de la factura "
        f"<b>{inv.invoice_id}</b> ({inv.vendor_name}, {eur(inv.amount_total)} €) "
        f"<b>no es la del maestro de proveedores</b>. Patrón compatible con "
        f"suplantación de proveedor (cambio de IBAN por email falso).</div></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div class='apct-card'><b>Diff contra el maestro de proveedores</b>"
        f"<table class='apct-table' style='margin-top:10px;'>"
        f"<tr><th></th><th>Maestro (fuente de verdad)</th><th>Factura recibida</th></tr>"
        f"<tr><td>IBAN</td>"
        f"<td><code>{vendor.iban}</code></td>"
        f"<td style='color:#C0392B;font-weight:700;'><code>{inv.iban_on_invoice}</code></td></tr>"
        f"<tr><td>Banco</td><td>{vendor.bank_name}</td>"
        f"<td style='color:#C0392B;'>desconocido (cuenta no registrada)</td></tr>"
        f"<tr><td>Proveedor</td><td colspan='2'>{vendor.name} · NIF {vendor.tax_id} · "
        f"proveedor habitual con historial limpio este mes</td></tr>"
        f"</table>"
        f"<div style='margin-top:12px;'><b>Acción recomendada:</b> "
        f"{exc.evidence.get('accion_recomendada')}</div>"
        f"<div style='margin-top:6px;color:#5A6572;'>Dueño sugerido: <b>{exc.owner}</b> · "
        f"El pago está retenido: esta factura no puede entrar a ningún lote.</div></div>",
        unsafe_allow_html=True,
    )


def _evidence_table(evidence: dict) -> str:
    rows = []
    for k, v in evidence.items():
        if v in (None, [], {}, ""):
            continue
        rows.append(f"<tr><td style='color:#5A6572;'>{k}</td><td>{v}</td></tr>")
    return "<table class='apct-table'>" + "".join(rows) + "</table>"


def render() -> None:
    st.markdown("## Cola de excepciones")
    if not run_is_ready():
        st.info("Corré el mes primero (vista **Corrida del mes**).")
        return
    ds = get_dataset()
    run = get_run()
    result = run["result"]
    invoices = {i.invoice_id: i for i in ds.invoices}

    st.markdown(
        f"<div class='apct-card'>El sistema bloqueó <b>{len(result.exceptions)} facturas "
        f"sin intervención humana</b>. Cada una tiene control, evidencia y dueño "
        f"sugerido: el equipo revisa excepciones, no el 100% de las facturas.</div>",
        unsafe_allow_html=True,
    )

    fraud = [e for e in result.exceptions if e.fraud_alert]
    for exc in fraud:
        _fraud_screen(exc, invoices[exc.invoice_id], ds.vendors[invoices[exc.invoice_id].vendor_id])

    others = [e for e in result.exceptions if not e.fraud_alert]
    for exc in others:
        inv = invoices[exc.invoice_id]
        title = (f"{exc.invoice_id} · {inv.vendor_name} · {eur(inv.amount_total)} € "
                 f"· {exc.control_id}")
        with st.expander(title):
            st.markdown(
                f"{badge(exc.control_id, 'block')} &nbsp; {badge('HARD', 'mut')} "
                f"&nbsp; dueño sugerido: <b>{exc.owner}</b>"
                f"<div style='margin:8px 0;color:#1A2332;'>{exc.detail}</div>",
                unsafe_allow_html=True,
            )
            st.markdown("**Evidencia (esperado vs recibido)**")
            st.markdown(_evidence_table(exc.evidence), unsafe_allow_html=True)
