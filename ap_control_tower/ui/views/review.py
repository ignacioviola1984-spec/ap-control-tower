"""Vista: Revision humana.

La cola de trabajo de los casos que requieren confirmacion humana SIN ser
liberacion de dinero. El humano interviene en dos lugares: confirma datos
ACA, libera dinero en "Aprobacion de pagos". Distincion visual en toda la
vista: pendiente de confirmacion humana (ambar) != bloqueada por control (rojo).
"""

from __future__ import annotations

import streamlit as st

from ...catalogs import PROJECT_CODES
from ...engine.review import ReviewError
from ...models import (
    STATUS_ANTICIPO_EXCEPCION,
    STATUS_ANTICIPO_PENDIENTE,
    STATUS_ANTICIPO_RETENIDO,
)
from ..state import (
    approve_anticipo_action,
    confirm_internal_data_action,
    get_dataset,
    get_run,
    run_is_ready,
)
from ..theme import badge, eur, status_badge

AMBAR = "#B7791F"


def _doctrina() -> None:
    st.html(
        "<div class='apct-card'><b>El humano interviene en dos lugares.</b> "
        "Acá <b>confirma datos</b> (centros de coste, aprobadores, presupuestos de "
        "anticipos); en <b>Aprobación de pagos</b> libera dinero. Confirmar datos "
        "NUNCA libera un pago: si una factura confirmada entra a un lote, ese lote "
        "pierde sus sign-offs, se revalida por los dos checkers y vuelve al gate."
        "<br><span style='color:#5A6572;'>Lo <b style='color:#B7791F;'>ámbar</b> espera "
        "una confirmación humana; lo <b style='color:#C0392B;'>rojo</b> está bloqueado "
        "por un control y vive en la cola de excepciones.</span></div>",
    )


def _pendientes_datos_internos(run, invoices) -> None:
    result = run["result"]
    pend = [r for r in result.retenciones if r.reason == "datos_internos"]
    st.html(f"#### (a) Pendientes de datos internos "
                f"{badge(f'{len(pend)} non-PO retenidas', 'flag')}")
    if not pend:
        st.html("<div class='apct-card' style='color:#5A6572;'>No hay facturas "
                    "esperando datos internos.</div>")
        return
    for r in pend:
        inv = invoices[r.invoice_id]
        with st.container(border=True):
            st.html(
                f"<div style='border-left:4px solid {AMBAR};padding-left:12px;'>"
                f"<b>{r.invoice_id} · {inv.vendor_name}</b> · {eur(inv.amount_total)} € · "
                f"{inv.description}<br>{badge('PENDIENTE DE CONFIRMACIÓN HUMANA', 'flag')} "
                f"&nbsp;falta: {', '.join(r.missing)}</div>",
            )
            st.html(
                f"<div style='margin:8px 0;color:#5A6572;font-size:13px;'>"
                f"<b>Propuesta del agente</b> · centro de coste "
                f"<code>{r.propuesta.get('cost_center_propuesto')}</code> · aprobador "
                f"<code>{r.propuesta.get('aprobador_propuesto')}</code> · justificación: "
                f"regla {r.propuesta.get('regla')}</div>",
            )
            c1, c2, c3 = st.columns(3)
            cc_options = list(PROJECT_CODES)
            cc_default = r.propuesta.get("cost_center_propuesto")
            cc = c1.selectbox(
                "Centro de coste", cc_options,
                index=cc_options.index(cc_default) if cc_default in cc_options else 0,
                key=f"cc_{r.invoice_id}",
                format_func=lambda c: f"{c} · {PROJECT_CODES[c]}")
            ap = c2.text_input("Aprobador interno",
                               value=r.propuesta.get("aprobador_propuesto") or "",
                               key=f"ap_{r.invoice_id}")
            ct = c3.text_input("Contrato / soporte", value="",
                               placeholder="p. ej. contrato, acta, email de encargo",
                               key=f"ct_{r.invoice_id}")
            c4, c5 = st.columns([1.4, 1])
            who = c4.text_input("Confirmado por (queda en el registro)",
                                key=f"who_{r.invoice_id}",
                                placeholder="Nombre y apellido")
            if c5.button("Confirmar datos y continuar el flujo", type="primary",
                         key=f"btn_{r.invoice_id}", use_container_width=True):
                try:
                    status = confirm_internal_data_action(
                        r.invoice_id, who, cc, ap, ct)
                    st.success(f"{r.invoice_id} confirmada por {who}: nuevo estado "
                               f"'{status}'. Registrado en el audit trail.")
                    st.rerun()
                except ReviewError as e:
                    st.error(f"Revisión: {e}")


def _anticipos(run, invoices) -> None:
    result = run["result"]
    anticipos = [o for o in result.outcomes.values() if o.status.startswith("anticipo")]
    st.html(f"#### (b) Anticipos / proformas "
                f"{badge(f'{len(anticipos)} en flujo propio', 'info')}")
    if not anticipos:
        st.html("<div class='apct-card' style='color:#5A6572;'>No hay proformas "
                    "este mes.</div>")
        return
    for o in anticipos:
        inv = invoices[o.invoice_id]
        with st.container(border=True):
            st.html(
                f"<b>{o.invoice_id} · {inv.vendor_name}</b> · {eur(inv.amount_total)} € · "
                f"{inv.description}<br>{status_badge(o.status)} &nbsp;"
                f"{badge('JAMÁS ENTRA A UN LOTE DE PAGO', 'mut')}",
            )
            if o.status == STATUS_ANTICIPO_RETENIDO:
                c1, c2 = st.columns([1.4, 1])
                who = c1.text_input("Aprobado por (queda en el registro)",
                                    key=f"ant_who_{o.invoice_id}")
                if c2.button("Aprobar anticipo", type="primary",
                             key=f"ant_btn_{o.invoice_id}", use_container_width=True):
                    try:
                        status = approve_anticipo_action(o.invoice_id, who)
                        st.success(f"Anticipo aprobado por {who}: estado '{status}'.")
                        st.rerun()
                    except ReviewError as e:
                        st.error(f"Revisión: {e}")
            elif o.status == STATUS_ANTICIPO_PENDIENTE:
                st.html(
                    "<span style='color:#5A6572;'>Presupuesto aprobado. "
                    "<b>Pendiente de factura final</b>: cuando llegue la factura "
                    "fiscal, se clasifica como factura y sigue el flujo normal.</span>")
            elif o.status == STATUS_ANTICIPO_EXCEPCION:
                st.html(
                    "<div style='border-left:4px solid #C0392B;padding-left:12px;"
                    "color:#5A6572;'><b style='color:#C0392B;'>Excepción C8 (bloqueada "
                    "por control):</b> el anticipo se pagó y no hay factura final "
                    "posterior vinculada. Dinero salido sin documento fiscal: vive "
                    "también en la cola de excepciones con su dueño sugerido.</div>")


def _vendor_master(run) -> None:
    ds = get_dataset()
    result = run["result"]
    incompletos = [v for v in ds.vendors.values()
                   if not (v.tax_id or "").strip() or not v.razon_social_confirmada]
    retenidas = [r for r in result.retenciones if r.reason == "alta_proveedor"]
    st.html(f"#### (c) Vendor master incompleto "
                f"{badge(f'{len(incompletos)} proveedores', 'flag' if incompletos else 'ok')}")
    if not incompletos:
        st.html(
            "<div class='apct-card' style='color:#5A6572;'>Maestro de proveedores "
            "completo: todos tienen tax_id y razón social confirmada. Si un alta "
            "quedara incompleta, sus facturas se retienen acá hasta completarla "
            "(los datos bancarios siempre con doble aprobación humana).</div>")
        return
    for v in incompletos:
        faltas = []
        if not (v.tax_id or "").strip():
            faltas.append("tax_id (CIF/NIF/VAT)")
        if not v.razon_social_confirmada:
            faltas.append("razón social legal confirmada")
        afectadas = [r.invoice_id for r in retenidas
                     if get_dataset().invoices and any(
                         i.vendor_id == v.vendor_id and i.invoice_id == r.invoice_id
                         for i in get_dataset().invoices)]
        st.html(
            f"<div class='apct-card' style='border-left:4px solid {AMBAR};'>"
            f"<b>{v.name}</b> ({v.vendor_id}) · falta: {', '.join(faltas)}"
            f"<br><span style='color:#5A6572;'>Retiene sus facturas hasta completar "
            f"el alta{(': ' + ', '.join(afectadas)) if afectadas else ''}. El alta o "
            f"cambio de datos bancarios exige doble aprobación humana.</span></div>")


def render() -> None:
    st.markdown("## Revisión humana · confirmar datos, no liberar dinero")
    _doctrina()
    if not run_is_ready():
        st.info("Corré el mes primero (vista **Corrida del mes**).")
        return
    ds = get_dataset()
    run = get_run()
    invoices = {i.invoice_id: i for i in ds.invoices}
    _pendientes_datos_internos(run, invoices)
    _anticipos(run, invoices)
    _vendor_master(run)