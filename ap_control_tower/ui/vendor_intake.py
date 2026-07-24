"""Alta de proveedor en el formato de importación de Sage.

El sistema no escribe en el ERP: genera la fila en las columnas del maestro de
Sage para importarla. Las validaciones (CIF, IBAN, duplicados) son lo que evita
las dos fallas caras del alta manual: duplicar un proveedor y registrar una
cuenta de cobro equivocada.
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st
from openpyxl import Workbook

from ..extraction.banking import is_valid_bic, is_valid_iban
from ..sage.vendor_master import normalize_supplier_name, normalize_tax_id
from .trial import session as sess

#: Columnas del export/importación de proveedores de Sage, en orden.
SAGE_COLUMNS = [
    "Código cuenta",
    "Descripción",
    "Clien./Prov.",
    "Sigla",
    "CIF/DNI",
    "Cód. divisa",
    "Ind. prorrata",
    "Bloqueada",
]

_PAISES = [
    "ES", "FR", "GB", "DE", "IT", "PT", "NL", "BE", "IE", "AT", "DK", "SE",
    "FI", "PL", "CZ", "GR", "HU", "RO", "US", "AR", "BR", "MX", "CH",
]
_DIVISAS = ["", "EUR", "USD", "GBP", "CHF"]


def _campo(valor) -> str:
    return str(valor or "").strip()


def _validar(datos: dict, master) -> tuple[list[str], list[str]]:
    """Devuelve (errores que impiden el alta, advertencias que no la impiden)."""
    errores: list[str] = []
    avisos: list[str] = []

    if not _campo(datos["descripcion"]):
        errores.append("La descripción (razón social) es obligatoria.")
    if not _campo(datos["cif"]):
        errores.append("El CIF/DNI es obligatorio.")

    iban = _campo(datos["iban"])
    if iban and not is_valid_iban(iban):
        errores.append("El IBAN no supera el dígito de control: revisalo.")
    bic = _campo(datos["bic"])
    if bic and not is_valid_bic(bic):
        avisos.append("El BIC/SWIFT no tiene un formato reconocible.")

    # Duplicados: es el problema real detectado en el maestro, donde varios
    # proveedores comparten CIF y vuelven ambigua la conciliación.
    if master is not None:
        clave = normalize_tax_id(datos["cif"])
        nombre = normalize_supplier_name(datos["descripcion"])
        if clave:
            iguales = [v for v in master.vendors if clave in v.tax_id_keys]
            if iguales:
                errores.append(
                    f"Ese CIF ya existe en el maestro como «{iguales[0].legal_name}» "
                    f"(cuenta {iguales[0].source_id}). Un duplicado vuelve ambigua "
                    "la conciliación de sus facturas."
                )
        if nombre and not errores:
            homonimos = [v for v in master.vendors if nombre in v.normalized_names]
            if homonimos:
                avisos.append(
                    f"Ya existe un proveedor con ese nombre: «{homonimos[0].legal_name}»."
                )
    return errores, avisos


def _fila_sage(datos: dict) -> dict:
    return {
        "Código cuenta": _campo(datos["codigo_cuenta"]),
        "Descripción": _campo(datos["descripcion"]),
        "Clien./Prov.": "Proveedor",
        "Sigla": _campo(datos["sigla"]),
        "CIF/DNI": _campo(datos["cif"]),
        "Cód. divisa": _campo(datos["divisa"]),
        "Ind. prorrata": _campo(datos["prorrata"]),
        "Bloqueada": "Sí" if datos["bloqueada"] else "No",
    }


def altas_xlsx(filas: list[dict]) -> bytes:
    """Plantilla de importación a Sage con una o varias altas.

    La hoja principal respeta las columnas del maestro. Los datos bancarios van
    en una hoja aparte porque el export actual de Sage todavía no trae columna
    de IBAN; así el archivo se importa sin romper el formato y el dato no se
    pierde mientras tanto.
    """
    book = Workbook()
    sheet = book.active
    sheet.title = "Proveedores"
    sheet.append(SAGE_COLUMNS)
    for fila in filas:
        sheet.append([fila.get(col, "") for col in SAGE_COLUMNS])

    bancarios = [f for f in filas if f.get("I.B.A.N.") or f.get("BIC/SWIFT")]
    if bancarios:
        extra_sheet = book.create_sheet("Datos bancarios")
        extra_sheet.append(["CIF/DNI", "I.B.A.N.", "BIC/SWIFT"])
        for fila in bancarios:
            extra_sheet.append([
                fila.get("CIF/DNI", ""), fila.get("I.B.A.N.", ""),
                fila.get("BIC/SWIFT", ""),
            ])
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def _plantilla_xlsx(fila: dict, extras: dict) -> bytes:
    return altas_xlsx([{
        **fila,
        "I.B.A.N.": extras.get("iban", ""),
        "BIC/SWIFT": extras.get("bic", ""),
    }])


def _formulario() -> dict | None:
    with st.form("_alta_proveedor", border=False, enter_to_submit=False):
        col1, col2 = st.columns(2)
        codigo_cuenta = col1.text_input(
            "Código cuenta", placeholder="Ej. 41000123",
            help="Dejalo vacío si Sage lo asigna al importar.")
        descripcion = col2.text_input(
            "Descripción", placeholder="Razón social del proveedor")

        col1, col2, col3 = st.columns(3)
        cif = col1.text_input("CIF/DNI", placeholder="Ej. B12345678")
        sigla = col2.selectbox("Sigla (país)", _PAISES, index=0)
        divisa = col3.selectbox("Cód. divisa", _DIVISAS, index=0)

        col1, col2, col3 = st.columns(3)
        prorrata = col1.text_input("Ind. prorrata", placeholder="Opcional")
        bloqueada = col2.checkbox("Bloqueada")
        col3.empty()

        st.markdown("###### Datos de cobro")
        col1, col2 = st.columns(2)
        iban = col1.text_input(
            "I.B.A.N.", placeholder="ES00 0000 0000 0000 0000 0000",
            help="Se valida el dígito de control. Habilita verificar después que "
                 "la cuenta de cada factura sea la registrada.")
        bic = col2.text_input("BIC/SWIFT", placeholder="Opcional")

        enviado = st.form_submit_button(
            "Validar y generar plantilla", type="primary",
            icon=":material/fact_check:", width="stretch")

    if not enviado:
        return None
    return {
        "codigo_cuenta": codigo_cuenta, "descripcion": descripcion,
        "cif": cif, "sigla": sigla, "divisa": divisa,
        "prorrata": prorrata, "bloqueada": bloqueada,
        "iban": iban, "bic": bic,
    }


def _vendor_exposure(session, master):
    """Exposición pendiente por proveedor vinculado, a partir de la sesión.

    Cruza los documentos ya extraídos con el maestro: da una vista de cuánto se
    le debe a cada proveedor sin exponer datos sensibles.
    """
    from decimal import Decimal, InvalidOperation

    from ..sage.vendor_master import normalize_tax_id
    from .pilot_format import supplier_name

    exposure: dict[str, dict] = {}
    for result in session.results:
        document = result.document
        nombre = supplier_name(document)
        clave = normalize_tax_id(document.get("proveedor_tax_id")) or nombre
        try:
            importe = Decimal(str(document.get("importe_total")))
        except (InvalidOperation, TypeError, ValueError):
            importe = Decimal("0")
        moneda = str(document.get("moneda") or "EUR").upper()
        entry = exposure.setdefault(
            clave, {"nombre": nombre, "docs": 0, "por_moneda": {}})
        entry["docs"] += 1
        entry["por_moneda"][moneda] = entry["por_moneda"].get(moneda, Decimal("0")) + importe
    return exposure


def vendor_entities(session) -> list[dict]:
    """Proveedores de la sesión con su estado frente al maestro. Función pura.

    Nunca devuelve IBAN, cuenta ni identificador fiscal completos: sólo el
    veredicto de cada control. Esta lista alimenta una pantalla, y una pantalla
    no necesita el dato en claro para decir si algo cuadra o no.
    """
    from ..persistence.masking import mask_iban, mask_tax_id
    from ..sage.vendor_master import normalize_tax_id
    from .pilot_format import document_state, supplier_name
    from .trial import workflow

    resolutions = getattr(session, "supplier_resolutions", {}) or {}
    results = workflow.unique_results(session.results)
    duplicates = workflow.duplicate_doc_ids(results)
    entidades: dict[str, dict] = {}
    for result in results:
        document = result.document
        nombre = supplier_name(document)
        clave = normalize_tax_id(document.get("proveedor_tax_id")) or nombre.casefold()
        resolution = resolutions.get(str(result.doc_id)) or {}
        state, reasons = document_state(
            result, session.review_decisions, session.approval_decisions, duplicates
        )
        entrada = entidades.setdefault(clave, {
            "nombre": nombre,
            "tax_id": mask_tax_id(document.get("proveedor_tax_id")) or "—",
            "estado_sage": resolution.get("status") or "sin_maestro",
            "codigo": resolution.get("vendor_code") or "—",
            "razon_social": resolution.get("vendor_legal_name") or nombre,
            "iban_coincide": resolution.get("iban_matches"),
            "cuenta": mask_iban(document.get("iban")) or "—",
            "documentos": [],
            "por_moneda": {},
            "motivos": set(),
        })
        entrada["documentos"].append({
            "doc_id": str(result.doc_id),
            "Número": str(document.get("numero_factura") or "—"),
            "Emisión": str(document.get("fecha_emision") or "—"),
            "Estado": state,
            "Importe": document.get("importe_total"),
            "Moneda": str(document.get("moneda") or "EUR").upper(),
        })
        importe = _importe(document.get("importe_total"))
        moneda = str(document.get("moneda") or "EUR").upper()
        entrada["por_moneda"][moneda] = entrada["por_moneda"].get(
            moneda, importe.__class__("0")) + importe
        for motivo in reasons:
            entrada["motivos"].add(str(motivo))
    return sorted(entidades.values(), key=lambda item: item["nombre"].casefold())


def _importe(value):
    from decimal import Decimal, InvalidOperation

    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


_SAGE_STATE_LABELS = {
    "matched": ("Vinculado en Sage", "ok"),
    "not_found": ("No dado de alta", "risk"),
    "inactive": ("Dado de baja", "warn"),
    "ambiguous": ("Vinculación ambigua", "warn"),
    "sin_maestro": ("Sin maestro aplicado", "muted"),
}


def _render_vendor_entity(entidad: dict) -> None:
    from . import design
    from .pilot_format import STATE_LABELS

    etiqueta, tono = _SAGE_STATE_LABELS.get(
        entidad["estado_sage"], (entidad["estado_sage"], "muted"))
    montos = " · ".join(
        design.money(value, currency)
        for currency, value in sorted(entidad["por_moneda"].items()))
    design.entity_header(
        entidad["nombre"],
        entidad["razon_social"] if entidad["razon_social"] != entidad["nombre"] else "",
        chips=[design.chip(etiqueta, tono)],
        meta=montos,
    )

    identidad, banca = st.columns(2, gap="medium")
    with identidad.container(border=True, height="stretch"):
        st.markdown("##### Identidad")
        st.write(f"**Cuenta contable en Sage:** {entidad['codigo']}")
        st.write(f"**Identificador fiscal:** {entidad['tax_id']}")
        st.write(f"**Documentos en la sesión:** {len(entidad['documentos'])}")
        st.caption("El identificador fiscal se muestra enmascarado.")
    with banca.container(border=True, height="stretch"):
        st.markdown("##### Datos de cobro")
        st.write(f"**Cuenta informada en las facturas:** {entidad['cuenta']}")
        coincide = entidad["iban_coincide"]
        if coincide is True:
            design.alert("La cuenta coincide con la registrada en el maestro.",
                         tone="ok", title="Sin cambio bancario")
        elif coincide is False:
            design.alert(
                "La cuenta de cobro NO coincide con la registrada en el maestro. "
                "Verificá el cambio por un canal distinto del correo antes de pagar.",
                tone="risk", title="Posible desvío de pago")
        else:
            st.caption(
                "El maestro exportado todavía no trae columna de IBAN, así que "
                "la comparación de cuenta no puede ejecutarse. El control queda "
                "activo y se dispara solo cuando el dato llegue."
            )

    st.markdown("##### Documentos asociados")
    st.dataframe(
        pd.DataFrame([
            {"Documento": item["doc_id"], "Número": item["Número"],
             "Emisión": item["Emisión"], "Moneda": item["Moneda"],
             "Importe": float(_importe(item["Importe"])),
             "Estado": STATE_LABELS.get(item["Estado"], item["Estado"])}
            for item in entidad["documentos"]
        ]),
        hide_index=True, width="stretch",
        column_config={
            "Importe": st.column_config.NumberColumn(
                "Importe", format="accounting", alignment="right"),
        },
    )
    if entidad["motivos"]:
        st.markdown("##### Estado documental")
        for motivo in sorted(entidad["motivos"]):
            st.warning(motivo, icon=":material/warning:")


def _render_pending_altas(session) -> None:
    from . import design
    from .pilot_format import format_datetime

    pendientes = list(getattr(session, "pending_vendors", []) or [])
    if not pendientes:
        design.empty_state(
            "Sin altas pendientes",
            "Los proveedores dados de alta en esta sesión aparecen acá hasta "
            "que se importan a Sage.",
        )
        return
    design.alert(
        f"{len(pendientes)} alta(s) viajan a Sage con el próximo lote de pago.",
        tone="warn", title="Pendiente de sincronizar",
    )
    st.dataframe(pendientes, hide_index=True, width="stretch")

    eventos = [
        {"when": format_datetime(event.ts),
         "what": "Alta de proveedor registrada",
         "who": event.agent, "tone": "ok"}
        for event in session.audit.events
        if str(event.action) == "alta-proveedor-registrada"
    ]
    if eventos:
        st.markdown("##### Historial de altas")
        design.timeline(eventos[-10:])

    st.download_button(
        "Exportar altas para Sage",
        data=altas_xlsx(pendientes),
        file_name="torre-control-altas-proveedor.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        icon=":material/download:",
        width="stretch",
        key=f"vendor_altas_workspace_{session.audit.run_id}",
    )


def render_new_vendor() -> None:
    from . import design

    design.page_header(
        "Proveedores",
        "Estado de cada proveedor frente al maestro de Sage, su exposición y "
        "sus altas pendientes.",
    )

    session = sess.get_session()
    master = getattr(session, "supplier_master", None)
    resumen = session.supplier_master_summary or {}
    pendientes = getattr(session, "pending_vendors", [])
    entidades = vendor_entities(session)

    cols = st.columns(4, gap="small")
    with cols[0]:
        design.kpi("En el maestro", resumen.get("active_vendors", 0),
                   help_text="Proveedores activos cargados desde Sage")
    with cols[1]:
        design.kpi("Con exposición en la sesión", len(entidades))
    with cols[2]:
        design.kpi(
            "Sin alta en Sage",
            sum(1 for item in entidades if item["estado_sage"] == "not_found"),
            help_text="Facturas de proveedores que el maestro no reconoce")
    with cols[3]:
        design.kpi("Altas pendientes de Sage", len(pendientes),
                   delta="enviar con el lote" if pendientes else None,
                   delta_color="inverse" if pendientes else "off")

    ficha, alta, altas = st.tabs(
        ["Ficha del proveedor", "Dar de alta", f"Altas pendientes · {len(pendientes)}"])

    with ficha:
        if not entidades:
            design.empty_state(
                "Todavía no hay proveedores en la sesión",
                "Aparecen acá en cuanto se procesa la primera factura.",
            )
        else:
            nombres = [item["nombre"] for item in entidades]
            elegido = st.selectbox(
                "Proveedor", nombres, key="_vendor_pick",
                help="Proveedores con documentos en esta sesión.")
            _render_vendor_entity(entidades[nombres.index(elegido)])

    with altas:
        _render_pending_altas(session)

    with alta:
        st.caption(
            "El alta queda disponible para conciliar de inmediato y viaja a "
            "Sage con el lote de pago."
        )
        datos = _formulario()
        if datos is None:
            return

        errores, avisos = _validar(datos, master)
        for error in errores:
            st.error(error, icon=":material/error:")
        for aviso in avisos:
            st.warning(aviso, icon=":material/warning:")
        if errores:
            return

        fila = _fila_sage(datos)
        extras = {"iban": _campo(datos["iban"]), "bic": _campo(datos["bic"])}
        sess.register_new_vendor(session, fila, extras)
        sess.persist(session)
        st.success(
            "Proveedor dado de alta. Ya concilia con las facturas de esta sesión "
            "y se enviará a Sage junto al lote de pago.",
            icon=":material/check_circle:",
        )
        st.dataframe([fila], hide_index=True, width="stretch")
        st.download_button(
            "Descargar plantilla de importación a Sage",
            data=_plantilla_xlsx(fila, extras),
            file_name=f"alta-proveedor-{normalize_tax_id(datos['cif']) or 'nuevo'}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
            width="stretch",
        )
