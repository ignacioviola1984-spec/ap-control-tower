"""Vista 4: Aprobacion de pagos: EL gate humano.

Lote del jueves con detalle por factura, los dos sign-offs agenticos con
timestamp, total a liberar y el flujo humano vivo: aprobar pide nombre y
registra decision + timestamp; rechazar devuelve el lote con motivo.
La aprobacion cambia el estado REAL del sistema (mismo RunResult que ven
todas las vistas). Imposible llegar a "liberado al banco" sin ella.
"""

from __future__ import annotations

import streamlit as st

from ...app import (
    ESTADO_APROBADO,
    ESTADO_DETENIDO,
    ESTADO_LIBERADO,
    ESTADO_PENDIENTE_HUMANO,
    ESTADO_RECHAZADO,
    GateViolation,
)
from ..state import (
    approve_and_release_action,
    close_batch_action,
    get_dataset,
    get_run,
    reject_batch_action,
    run_is_ready,
)
from ..theme import badge, eur

STATE_CHIP = {
    ESTADO_PENDIENTE_HUMANO: ("Pendiente de aprobación humana", "gate"),
    ESTADO_APROBADO: ("Aprobado · listo para liberar", "info"),
    ESTADO_LIBERADO: ("LIBERADO AL BANCO", "gate"),
    ESTADO_RECHAZADO: ("Rechazado · lote devuelto", "flag"),
    ESTADO_DETENIDO: ("Detenido por checker", "block"),
}


def _signoff_card(title: str, so) -> str:
    if so is None:
        return (f"<div class='apct-card'><b>{title}</b><br>"
                f"{badge('PENDIENTE', 'mut')}</div>")
    verdict = badge("FIRMADO", "ok") if so.ok else badge("DETIENE EL LOTE", "block")
    return (f"<div class='apct-card'><b>{title}</b> {verdict}"
            f"<div style='color:#5A6572;font-size:13px;margin-top:6px;'>{so.detail}</div>"
            f"<div style='color:#5A6572;font-size:11.5px;margin-top:6px;'>"
            f"{so.checker}<br>firmado: <code>{so.ts}</code></div></div>")


def render() -> None:
    st.markdown("## Aprobación de pagos · el único gate humano")
    st.html(
        "<div class='apct-card'><b>“El sistema se auto-bloquea ante alertas. La "
        "aprobación para liberar dinero es siempre humana.”</b><br>"
        "<span style='color:#5A6572;'>El humano interviene en dos lugares: confirma "
        "datos en <b>Revisión humana</b>, libera dinero ACÁ. Cada lote llega con el "
        "doble sign-off agéntico ya firmado: el checker A revalidó cada factura contra "
        "todos los controles de su ruta y el checker B validó el agregado. Nada se "
        "libera al banco sin una aprobación humana con nombre, decisión y timestamp. "
        "El lote contiene únicamente <b>transferencias</b>.</span></div>",
    )
    if not run_is_ready():
        st.info("Corré el mes primero (vista **Corrida del mes**).")
        return

    ds = get_dataset()
    run = get_run()
    result = run["result"]
    workflows = run["workflows"]
    invoices = {i.invoice_id: i for i in ds.invoices}

    # Que quedo FUERA del lote y por que (coherente con el motor v2)
    dd = [o for o in result.outcomes.values() if o.status == "domiciliacion_pendiente_conciliacion"]
    tj = [o for o in result.outcomes.values() if o.status == "tarjeta_pendiente_conciliacion"]
    anticipos = [o for o in result.outcomes.values() if o.status.startswith("anticipo")]
    fuera = []
    if dd:
        fuera.append(f"<b>{len(dd)} domiciliación(es)</b> ({', '.join(o.invoice_id for o in dd)}): "
                     f"van por conciliación post-débito, no por el lote")
    if tj:
        fuera.append(f"<b>{len(tj)} pago(s) con tarjeta</b> ({', '.join(o.invoice_id for o in tj)}): "
                     f"van por conciliación contra extracto")
    if result.retenciones:
        fuera.append(f"<b>{len(result.retenciones)} retenida(s)</b> "
                     f"({', '.join(r.invoice_id for r in result.retenciones)}): "
                     f"pendientes de confirmación humana en Revisión humana, no entran")
    if anticipos:
        fuera.append(f"<b>{len(anticipos)} anticipo(s)/proforma(s)</b> "
                     f"({', '.join(o.invoice_id for o in anticipos)}): "
                     f"flujo propio, jamás entran a un lote")
    blocked_n = sum(1 for o in result.outcomes.values() if o.status == "bloqueada")
    if blocked_n:
        fuera.append(f"<b>{blocked_n} bloqueada(s) por control</b>: en la cola de excepciones")
    if fuera:
        st.html(
            "<div class='apct-card' style='border-left:4px solid #B7791F;'>"
            "<b>Fuera de los lotes de este mes (solo entran transferencias validadas):</b>"
            "<ul style='margin:6px 0 0 0;padding-left:18px;color:#5A6572;'>"
            + "".join(f"<li>{x}</li>" for x in fuera) + "</ul></div>",
        )

    dates = [b.batch_date.isoformat() for b in result.batches]
    chosen = st.radio("Lote del jueves", dates, horizontal=True,
                      format_func=lambda d: f"jueves {d}")
    wf = workflows[chosen]
    batch = wf.batch
    closing = run["closing_reports"].get(chosen)

    label, kind = STATE_CHIP.get(wf.state, (wf.state, "mut"))
    if closing:
        label, kind = "Cerrado: pagos conciliados contra el pasivo", "ok"
    st.html(
        f"<div class='apct-card' style='display:flex;justify-content:space-between;"
        f"align-items:center;'><div><span style='font-size:19px;font-weight:800;'>"
        f"Lote del jueves {chosen}</span><br><span style='color:#5A6572;'>"
        f"{batch.count} facturas · {len({invoices[i].vendor_id for i in batch.invoice_ids})} "
        f"proveedores</span></div>"
        f"<div style='text-align:right;'><div style='font-size:24px;font-weight:800;'>"
        f"{eur(batch.total)} €</div>{badge(label, kind)}</div></div>",
    )

    ca, cb = st.columns(2)
    ca.html(_signoff_card("Sign-off A · revalidación factura por factura",
                              wf.sign_off_a))
    cb.html(_signoff_card("Sign-off B · validación del agregado",
                              wf.sign_off_b))

    rows = []
    for inv_id in batch.invoice_ids:
        inv = invoices[inv_id]
        o = result.outcomes[inv_id]
        flags = " ".join(badge(f, "flag") for f in o.flags) or "—"
        rows.append(f"<tr><td><b>{inv_id}</b></td><td>{inv.vendor_name}</td>"
                    f"<td>{inv.invoice_number}</td><td class='num'>"
                    f"{eur(inv.amount_total)} €</td><td>{flags}</td>"
                    f"<td><code>{inv.iban_on_invoice[:8]}…</code></td></tr>")
    st.html("<table class='apct-table'><tr><th>Factura</th><th>Proveedor</th>"
                "<th>Número</th><th>Importe</th><th>Flags</th><th>Cuenta destino</th></tr>"
                + "".join(rows) + "</table>")

    st.markdown("")

    # ---------------- el flujo humano vivo ----------------
    if wf.state == ESTADO_PENDIENTE_HUMANO:
        col_ap, col_re = st.columns(2)
        with col_ap:
            st.markdown("##### Aprobar y liberar al banco")
            name = st.text_input("Nombre del aprobador", key=f"ap_name_{chosen}",
                                 placeholder="Nombre y apellido (queda en el registro)")
            if st.button(f"Aprobar y liberar {eur(batch.total)} €", type="primary",
                         key=f"ap_btn_{chosen}", use_container_width=True):
                try:
                    decision = approve_and_release_action(chosen, name)
                    st.success(f"Lote liberado al banco. Aprobado por "
                               f"**{decision.approver}** · {decision.ts}")
                    st.rerun()
                except GateViolation as e:
                    st.error(f"Gate: {e}")
        with col_re:
            st.markdown("##### Rechazar y devolver el lote")
            rname = st.text_input("Nombre de quien decide", key=f"re_name_{chosen}")
            reason = st.text_input("Motivo del rechazo", key=f"re_reason_{chosen}",
                                   placeholder="p. ej. revisar prioridad con Tesorería")
            if st.button("Rechazar lote", key=f"re_btn_{chosen}",
                         use_container_width=True):
                try:
                    reject_batch_action(chosen, rname, reason)
                    st.rerun()
                except GateViolation as e:
                    st.error(f"Gate: {e}")

    elif wf.state == ESTADO_LIBERADO:
        d = wf.human_decision
        st.html(
            f"<div class='apct-card' style='border-left:4px solid #1E8E5A;'>"
            f"<b>Liberado al banco</b> · aprobado por <b>{d.approver}</b> · "
            f"decisión: {d.decision} · <code>{d.ts}</code><br>"
            f"<span style='color:#5A6572;'>La decisión quedó en el registro de "
            f"auditoría con nombre, decisión y timestamp.</span></div>",
        )
        if closing is None:
            if st.button("Ejecutar cierre contable (conciliar pago vs pasivo)",
                         key=f"close_{chosen}", type="primary"):
                close_batch_action(chosen)
                st.rerun()

    elif wf.state == ESTADO_RECHAZADO:
        d = wf.human_decision
        st.html(
            f"<div class='apct-card' style='border-left:4px solid #B7791F;'>"
            f"<b>Lote devuelto</b> por <b>{d.approver}</b> · <code>{d.ts}</code><br>"
            f"Motivo: <i>{d.reason}</i><br><span style='color:#5A6572;'>Las "
            f"{batch.count} facturas quedaron en estado <b>lote_devuelto</b>; el "
            f"estado se refleja en todo el sistema.</span></div>",
        )

    if closing is not None:
        st.markdown("##### Cierre: conciliación pago vs pasivo")
        st.html(
            f"<div class='apct-card'>{badge('CONCILIADO', 'ok')} "
            f"{closing.liabilities_cancelled} pagos matcheados contra su pasivo y "
            f"cancelados · total {eur(closing.total_paid)} € · "
            f"excepciones de cierre: <b>{len(closing.exceptions)}</b>"
            f"<div style='color:#5A6572;margin-top:6px;'>El double-check del cierre "
            f"ya no es el mismo equipo revisándose a sí mismo: es conciliación "
            f"automática con reporte de excepciones.</div></div>",
        )
