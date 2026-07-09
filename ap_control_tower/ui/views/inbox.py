"""Vista 1: INBOX / Corrida del mes.

"Procesar mes" corre el MOTOR REAL factura por factura (MonthRunner), con
progreso visible y velocidad regulable. No es un video: cada fila que aparece
es el pipeline procesando esa factura en ese momento.
"""

from __future__ import annotations

import time
from decimal import Decimal

import streamlit as st

from ...engine.pipeline import MonthRunner
from ...models import STATUS_BLOQUEADA, STATUS_EN_LOTE
from ..state import get_dataset, get_run, reset_run, run_is_ready, store_run
from ..theme import badge, eur, kpi, status_badge

SPEEDS = {
    "Rápido (~40 s)": 0.9,
    "Reunión (~3 min)": 4.5,
    "Instantáneo": 0.0,
}


def _feed_row(inv, outcome) -> str:
    if outcome.status == STATUS_BLOQUEADA:
        b = badge(f"BLOQUEADA · {outcome.blocking_control}", "block")
    elif outcome.flags:
        b = badge("VALIDADA CON FLAG", "flag") + " " + " ".join(
            badge(f, "flag") for f in outcome.flags)
    else:
        b = badge("VALIDADA", "ok")
    return (f"<tr><td><b>{inv.invoice_id}</b></td><td>{inv.vendor_name}</td>"
            f"<td>{inv.invoice_number}</td><td>{inv.received_date.isoformat()}</td>"
            f"<td class='num'>{eur(inv.amount_total)} €</td><td>{b}</td></tr>")


def _run_live(delay: float) -> None:
    ds = get_dataset()
    runner = MonthRunner(ds)
    progress = st.progress(0.0, text="Iniciando corrida...")
    stage = st.empty()
    feed = st.empty()
    rows: list[str] = []
    ok = flagged = blocked = 0
    while (step := runner.process_next()) is not None:
        inv, outcome = step
        if outcome.status == STATUS_BLOQUEADA:
            blocked += 1
        elif outcome.flags:
            flagged += 1
            ok += 1
        else:
            ok += 1
        n_controls = len(outcome.control_results)
        progress.progress(
            runner.processed_count / runner.total_invoices,
            text=(f"Procesando {inv.invoice_id} · {inv.vendor_name} · "
                  f"{eur(inv.amount_total)} € · {n_controls} controles ejecutados"),
        )
        stage.markdown(
            f"<div class='apct-card'>Validadas: <b>{ok}</b> &nbsp;·&nbsp; "
            f"con flag: <b>{flagged}</b> &nbsp;·&nbsp; "
            f"bloqueadas: <b style='color:#C0392B;'>{blocked}</b></div>",
            unsafe_allow_html=True,
        )
        rows.insert(0, _feed_row(inv, outcome))
        feed.markdown(
            "<table class='apct-table'><tr><th>Factura</th><th>Proveedor</th>"
            "<th>Número</th><th>Recibida</th><th>Importe</th><th>Resultado</th></tr>"
            + "".join(rows[:10]) + "</table>",
            unsafe_allow_html=True,
        )
        if delay:
            time.sleep(delay)
    result = runner.finalize()
    progress.progress(1.0, text="Corrida completa. Corriendo checkers de lote...")
    store_run(result, runner.audit, runner.ctx)
    st.rerun()


def _dashboard() -> None:
    run = get_run()
    result = run["result"]
    ds = get_dataset()
    invoices = {i.invoice_id: i for i in ds.invoices}

    blocked = [o for o in result.outcomes.values() if o.status == STATUS_BLOQUEADA]
    blocked_amount = sum((invoices[o.invoice_id].amount_total for o in blocked), Decimal("0"))
    batch_total = sum((b.total for b in result.batches), Decimal("0"))
    in_batches = sum(len(b.invoice_ids) for b in result.batches)
    flagged = [o for o in result.outcomes.values() if o.flags]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown(kpi("Facturas del mes", str(len(result.outcomes)),
                    "junio 2026 · 4 jueves de pago"), unsafe_allow_html=True)
    c2.markdown(kpi("En lotes de pago", f"{in_batches}",
                    f"{eur(batch_total)} € propuestos"), unsafe_allow_html=True)
    c3.markdown(kpi("Bloqueadas (sin humano)", str(len(blocked)),
                    f"{eur(blocked_amount)} € retenidos"), unsafe_allow_html=True)
    c4.markdown(kpi("Con flag soft", str(len(flagged)),
                    "avanzan, marcadas para revisión"), unsafe_allow_html=True)
    c5.markdown(kpi("Próximo ciclo", str(len(result.carryover_ids)),
                    "sin jueves restante en el mes"), unsafe_allow_html=True)

    st.markdown("#### Lotes del jueves")
    rows = []
    for b in result.batches:
        wf = run["workflows"][b.batch_date.isoformat()]
        state_map = {
            "pendiente_aprobacion_humana": badge("Pendiente gate humano", "gate"),
            "aprobado": badge("Aprobado", "info"),
            "liberado_al_banco": badge("Liberado al banco", "gate"),
            "rechazado": badge("Rechazado / devuelto", "flag"),
            "detenido_por_checker": badge("Detenido por checker", "block"),
        }
        closed = run["closing_reports"].get(b.batch_date.isoformat())
        chip = (badge("Cerrado y conciliado", "ok") if closed
                else state_map.get(wf.state, badge(wf.state, "mut")))
        rows.append(f"<tr><td><b>jueves {b.batch_date.isoformat()}</b></td>"
                    f"<td class='num'>{b.count}</td>"
                    f"<td class='num'>{eur(b.total)} €</td><td>{chip}</td></tr>")
    st.markdown("<table class='apct-table'><tr><th>Lote</th><th>Facturas</th>"
                "<th>Total</th><th>Estado</th></tr>" + "".join(rows) + "</table>",
                unsafe_allow_html=True)

    st.markdown("#### Todas las facturas")
    frows = []
    for inv in ds.invoices:
        o = result.outcomes[inv.invoice_id]
        flags = " ".join(badge(f, "flag") for f in o.flags)
        lote = o.batch_date.isoformat() if o.batch_date else "—"
        ctrl = o.blocking_control or "—"
        frows.append(
            f"<tr><td><b>{inv.invoice_id}</b></td><td>{inv.vendor_name}</td>"
            f"<td>{inv.invoice_number}</td><td>{inv.received_date.isoformat()}</td>"
            f"<td class='num'>{eur(inv.amount_total)} €</td>"
            f"<td>{status_badge(o.status)}</td><td>{ctrl}</td><td>{flags or '—'}</td>"
            f"<td>{lote}</td></tr>")
    st.markdown(
        "<table class='apct-table'><tr><th>Factura</th><th>Proveedor</th><th>Número</th>"
        "<th>Recibida</th><th>Importe</th><th>Estado</th><th>Control</th><th>Flags</th>"
        "<th>Lote</th></tr>" + "".join(frows) + "</table>",
        unsafe_allow_html=True,
    )


def render() -> None:
    st.markdown("## Corrida del mes")
    st.markdown(
        "<div class='apct-card'>El motor procesa el <b>inbox de junio 2026</b> "
        "(36 facturas recibidas por email con su OC) por el pipeline maker-checker "
        "C1–C7. Los controles hard bloquean solos; el único gate humano es la "
        "liberación del lote de pago.</div>",
        unsafe_allow_html=True,
    )
    col_btn, col_speed, col_reset = st.columns([1.2, 1.2, 1])
    speed = col_speed.selectbox("Velocidad del replay", list(SPEEDS), index=0,
                                label_visibility="collapsed")
    if col_btn.button("▶ Procesar mes (corrida en vivo)", type="primary",
                      use_container_width=True):
        _run_live(SPEEDS[speed])
    if run_is_ready():
        if col_reset.button("↺ Reprocesar desde cero", use_container_width=True):
            reset_run()
            st.rerun()
        _dashboard()
    else:
        st.info("Todavía no hay corrida en esta sesión. Elegí la velocidad y presioná "
                "**Procesar mes** para ver al sistema trabajar factura por factura.")
