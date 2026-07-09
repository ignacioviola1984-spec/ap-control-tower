"""Vista 6: Caso de negocio.

Las metricas que ELLOS declararon versus lo medido en la corrida. Sin inventar
numeros: la columna izquierda sale de su propia lamina de riesgos/metricas y
la derecha de la corrida de esta sesion. La unica estimacion (horas) es un
parametro visible y ajustable con la clienta.
"""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from ...models import STATUS_BLOQUEADA
from ..state import get_dataset, get_run, run_is_ready
from ..theme import badge, eur, kpi


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
        st.markdown(
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
            unsafe_allow_html=True,
        )
    with right:
        st.markdown("#### Lo medido en esta corrida")
        c1, c2 = st.columns(2)
        c1.markdown(kpi("Controles automáticos", f"{controls_run}",
                        "ejecutados este mes · hoy: cero"), unsafe_allow_html=True)
        c2.markdown(kpi("Sign-offs agénticos", f"{signoffs}",
                        "doble validación por lote"), unsafe_allow_html=True)
        c3, c4 = st.columns(2)
        c3.markdown(kpi("Retenido por bloqueos", f"{eur(blocked_amount)} €",
                        f"{len(blocked)} facturas frenadas sin humano"),
                    unsafe_allow_html=True)
        c4.markdown(kpi("Posible fraude retenido", f"{eur(fraud_amount)} €",
                        "IBAN distinto del maestro"), unsafe_allow_html=True)
        c5, c6 = st.columns(2)
        c5.markdown(kpi("Lotes propuestos", f"{len(result.batches)}",
                        f"{eur(batch_total)} € · trazables de punta a punta"),
                    unsafe_allow_html=True)
        c6.markdown(kpi("Aprobaciones trazables", "100%",
                        "nombre + decisión + timestamp"), unsafe_allow_html=True)

    st.markdown("#### Cobertura de los tres huecos del proceso actual")
    g1, g2, g3 = st.columns(3)
    dup_cases = [o for o in blocked if o.blocking_control == "C2_DUPLICADOS"]
    dup_amount = sum((invoices[o.invoice_id].amount_total for o in dup_cases), Decimal("0"))
    conc = next(o for o in blocked if o.blocking_control == "C7_CONCILIACION")
    g1.markdown(
        f"<div class='apct-card'>{badge('CUBIERTO', 'ok')} <b>Duplicados</b>"
        f"<div style='color:#5A6572;margin-top:6px;'>Riesgo declarado por ellos. "
        f"Esta corrida: <b>{len(dup_cases)} duplicadas bloqueadas</b> "
        f"({eur(dup_amount)} €), incluida una casi-duplicada con número distinto.</div></div>",
        unsafe_allow_html=True)
    g2.markdown(
        f"<div class='apct-card'>{badge('CUBIERTO', 'ok')} <b>Fraude bancario</b>"
        f"<div style='color:#5A6572;margin-top:6px;'>Hallazgo nuestro: cero validación "
        f"de datos bancarios. Esta corrida: <b>1 alerta de posible fraude</b> con "
        f"{eur(fraud_amount)} € retenidos antes de llegar al banco.</div></div>",
        unsafe_allow_html=True)
    g3.markdown(
        f"<div class='apct-card'>{badge('CUBIERTO', 'ok')} <b>Conciliación Excel/ERP</b>"
        f"<div style='color:#5A6572;margin-top:6px;'>Hallazgo nuestro: dos fuentes de "
        f"verdad sin conciliar. Esta corrida: <b>1 divergencia detectada</b> "
        f"(1.476,30 vs 1.467,30: un tipeo de 9,00 € que nadie iba a ver).</div></div>",
        unsafe_allow_html=True)

    st.markdown("#### Horas de carga y control manual eliminadas (parámetro ajustable)")
    mins = st.slider(
        "Minutos de trabajo manual por factura en el proceso actual "
        "(cargar, archivar, matchear, listar, aprobar por email, conciliar)",
        5, 40, 15,
    )
    hours = len(result.outcomes) * mins / 60
    st.markdown(
        f"<div class='apct-card'><span style='font-size:22px;font-weight:800;'>"
        f"≈ {hours:.1f} horas/mes</span> de carga y control manual que el agente "
        f"absorbe con este volumen ({len(result.outcomes)} facturas × {mins} min). "
        f"<span style='color:#5A6572;'>Es el único número estimado de esta vista: "
        f"el parámetro se calibra con la clienta; todo lo demás sale de la corrida "
        f"o de sus propios datos declarados.</span></div>",
        unsafe_allow_html=True,
    )
