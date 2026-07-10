"""Vista 3: Cola de excepciones, agrupada por severidad.

Las bloqueadas con el control que las freno, la evidencia (esperado vs
recibido) y el dueno sugerido, agrupadas por nivel de severidad con el conteo
en el encabezado de cada grupo. El fraude bancario tiene card de alerta
dedicada con el diff contra el maestro de proveedores.

Asignacion de severidad por control (criterio de presentacion, documentado):
  MAXIMA  C6 datos bancarios: patron de fraude por suplantacion; riesgo de
          transferir dinero a una cuenta ajena al proveedor.
  ALTA    C2 duplicados / casi-duplicados y C3 OC sin autorizacion o sin
          saldo: riesgo inminente de pago indebido con documentacion completa.
          C5 match cuando la diferencia supera 2x la materialidad (magnitud
          grande: no es un desvio, es otra cifra).
  MEDIA   C5 match sobre materialidad pero <= 2x (desvio material acotado);
          C7 divergencia de conciliacion cashflow vs ERP (inconsistencia de
          registro a corregir antes de pagar); C1 completitud documental
          (falta un adjunto: se reclama y se reintenta, no hay riesgo de pago
          inmediato porque la factura ni siquiera entra al circuito).
"""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from ...config import Controls
from ..state import get_dataset, get_run, run_is_ready
from ..theme import badge, eur

SEVERITY_ORDER = ["maxima", "alta", "media"]
SEVERITY_LABELS = {
    "maxima": "Severidad máxima",
    "alta": "Severidad alta",
    "media": "Severidad media",
}
SEVERITY_BADGE_KIND = {"maxima": "block", "alta": "block", "media": "flag"}
SEVERITY_COLOR = {"maxima": "#7B1D12", "alta": "#C0392B", "media": "#B7791F"}


def exception_severity(exc) -> str:
    """Severidad de presentacion segun el control que bloqueo (ver docstring)."""
    if exc.control_id == Controls.C6_DATOS_BANCARIOS:
        return "maxima"
    if exc.control_id in (Controls.C2_DUPLICADOS, Controls.C3_AUTORIZACION_OC,
                          Controls.C8_ANTICIPO_SIN_FACTURA_FINAL,
                          Controls.C11_MANDATO_DOMICILIACION):
        # anticipo pagado sin factura final = dinero salido sin documento
        # fiscal; domiciliacion sin mandato = debito sin autorizacion: alta
        return "alta"
    if exc.control_id == Controls.C5_MATCH:
        pct = Decimal(str(exc.evidence.get("diferencia_pct", "0")))
        materiality = Decimal(str(exc.evidence.get("materialidad_pct", "5")))
        return "alta" if pct > 2 * materiality else "media"
    return "media"  # C1 completitud, C7 conciliacion


def _fraud_card(exc, inv, vendor) -> None:
    st.markdown(
        f"<div class='apct-fraud'>"
        f"<h3>⚠ ALERTA DE POSIBLE FRAUDE</h3>"
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
        f"sin intervención humana</b>, agrupadas acá por severidad. Cada una tiene "
        f"control, evidencia y dueño sugerido: el equipo revisa excepciones, no el "
        f"100% de las facturas.</div>",
        unsafe_allow_html=True,
    )

    groups: dict[str, list] = {s: [] for s in SEVERITY_ORDER}
    for exc in result.exceptions:
        groups[exception_severity(exc)].append(exc)

    for sev in SEVERITY_ORDER:
        items = groups[sev]
        if not items:
            continue
        n = len(items)
        st.markdown(
            f"<div style='margin:18px 0 8px 0;padding:8px 14px;border-left:5px solid "
            f"{SEVERITY_COLOR[sev]};background:#fff;border-radius:6px;"
            f"font-size:16px;font-weight:800;'>"
            f"{SEVERITY_LABELS[sev]}: {n} factura{'s' if n != 1 else ''}</div>",
            unsafe_allow_html=True,
        )
        for exc in items:
            inv = invoices[exc.invoice_id]
            if exc.fraud_alert:
                _fraud_card(exc, inv, ds.vendors[inv.vendor_id])
                continue
            title = (f"{exc.invoice_id} · {inv.vendor_name} · {eur(inv.amount_total)} € "
                     f"· {exc.control_id}")
            with st.expander(title):
                st.markdown(
                    f"{badge(exc.control_id, SEVERITY_BADGE_KIND[sev])} &nbsp; "
                    f"{badge(SEVERITY_LABELS[sev].upper(), SEVERITY_BADGE_KIND[sev])} "
                    f"&nbsp; dueño sugerido: <b>{exc.owner}</b>"
                    f"<div style='margin:8px 0;color:#1A2332;'>{exc.detail}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown("**Evidencia (esperado vs recibido)**")
                st.markdown(_evidence_table(exc.evidence), unsafe_allow_html=True)
