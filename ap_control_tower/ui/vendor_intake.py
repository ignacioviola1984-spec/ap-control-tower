"""Alta de proveedor en el formato de importación de Sage.

El sistema no escribe en el ERP: genera la fila en las columnas del maestro de
Sage para importarla. Las validaciones (CIF, IBAN, duplicados) son lo que evita
las dos fallas caras del alta manual: duplicar un proveedor y registrar una
cuenta de cobro equivocada.
"""

from __future__ import annotations

import io

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


def render_new_vendor() -> None:
    st.title("Nuevo proveedor")
    st.caption(
        "Da de alta un proveedor que no está en el maestro. Queda disponible "
        "para conciliar de inmediato y viaja a Sage con el lote de pago."
    )

    session = sess.get_session()
    master = getattr(session, "supplier_master", None)
    if master is not None:
        resumen = session.supplier_master_summary or {}
        st.caption(
            f"Maestro activo: {resumen.get('active_vendors', 0)} proveedor(es)."
        )
    pendientes = getattr(session, "pending_vendors", [])
    if pendientes:
        st.info(
            f"{len(pendientes)} alta(s) pendiente(s) de enviar a Sage con el "
            "próximo lote de pago.",
            icon=":material/pending_actions:",
        )

    with st.expander(
        "Datos del proveedor", expanded=True, icon=":material/person_add:"
    ):
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
        "Proveedor dado de alta. Ya concilia con las facturas de esta sesión y "
        "se enviará a Sage junto al lote de pago.",
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
