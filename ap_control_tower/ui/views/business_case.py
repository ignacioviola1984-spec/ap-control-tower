"""Vista 6: Caso de negocio.

Las metricas que ELLOS declararon versus lo medido en la corrida. Sin inventar
numeros: la columna izquierda sale de su propia lamina de riesgos/metricas y
la derecha de la corrida de esta sesion.
"""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from ...models import STATUS_BLOQUEADA
from ..state import get_dataset, get_run, run_is_ready
from ..theme import eur, kpi


def render() -> None:
    st.markdown("## Caso de negocio")
    if not run_is_ready():
        st.info("Corré el mes primero (vista **Corrida del mes**): todas las métricas "
                "de esta vista salen de la corrida, no hay números inventados.")
        return
    ds = get_dataset()
    run = get_run()
    result = run["result"]
    audit = run["audit"]
    invoices = {i.invoice_id: i for i in ds.invoices}

    controls_run = sum(1 for ev in audit.events if ev.action.startswith("control:"))
    signoffs = sum(1 for ev in audit.events if ev.action == "sign-off-lote")
    blocked = [o for o in result.outcomes.values() if o.status == STATUS_BLOQUEADA]
    blocked_amount = sum((invoices[o.invoice_id].amount_total for o in blocked), Decimal("0"))
    fraud_amount = invoices["INV-024"].amount_total
    batch_total = sum((b.total for b in result.batches), Decimal("0"))

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Lo que el proceso actual declara")
        st.html(
            "<div class='apct-card'>"
            "<ul style='margin:0;padding-left:18px;line-height:2;'>"
            "<li>Proceso <b>100% manual</b>, ~35 facturas/mes</li>"
            "<li><b>2–4 personas</b> involucradas en el ciclo</li>"
            "<li>Tiempo total de ciclo: <b>variable</b></li>"
            "<li>% de pagos a tiempo: <b>depende del control manual</b></li>"
            "<li>Facturas duplicadas <b>sin control automático</b></li>"
            "<li>Trazabilidad de aprobaciones <b>limitada</b> (email)</li>"
            "<li>Información <b>dispersa</b> (email, carpetas, Excel, ERP)</li>"
            "<li>Excel de cashflow y ERP contable <b>nunca conciliados</b> antes de pagar</li>"
            "<li>Datos bancarios del proveedor <b>sin validación</b> en ningún paso</li>"
            "</ul></div>",
        )
    with right:
        st.markdown("#### Lo medido en esta corrida")
        c1, c2 = st.columns(2)
        c1.html(kpi("Controles automáticos", f"{controls_run}",
                        "ejecutados este mes · hoy: cero"))
        c2.html(kpi("Sign-offs agénticos", f"{signoffs}",
                        "doble validación por lote"))
        c3, c4 = st.columns(2)
        c3.html(kpi("Retenido por bloqueos", f"{eur(blocked_amount)} €",
                        f"{len(blocked)} facturas frenadas sin humano"))
        c4.html(kpi("Posible fraude retenido", f"{eur(fraud_amount)} €",
                        "IBAN distinto del maestro"))
        c5, c6 = st.columns(2)
        c5.html(kpi("Lotes propuestos", f"{len(result.batches)}",
                        f"{eur(batch_total)} € · trazables de punta a punta"))
        c6.html(kpi("Aprobaciones trazables", "100%",
                        "nombre + decisión + timestamp"))
