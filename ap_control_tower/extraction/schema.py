"""Esquema de extraccion v2: la unica fuente de verdad de campos y enums.

Ajustado a partir del analisis de facturas reales del cliente: la realidad
trae facturas sin PO, proformas que no son facturas, metodos de pago variados
y datos fiscales incompletos. document_type va PRIMERO: clasificar el
documento es parte del output evaluado.

Regla transversal: campo no visible = null, NUNCA inferido. Si un dato esta
presente pero ilegible, va null y el nombre del campo se agrega a
campos_ilegibles (distingue "no esta" de "esta pero ilegible").
"""

from __future__ import annotations

import re
from datetime import date, timedelta

# ------------------------------------------------------------------ enums
DOCUMENT_TYPES = ("invoice", "proforma_or_advance_request", "other")
TRATAMIENTOS_IVA = ("nacional", "intracomunitario_inversion_sujeto_pasivo",
                    "no_desglosado", "exento_otro")
METODOS_PAGO = ("transferencia", "domiciliacion_direct_debit", "tarjeta",
                "no_indicado")

# ------------------------------------------------------------------ campos
# Orden canonico del output. document_type SIEMPRE primero.
FIELD_ORDER = [
    "document_type",
    "proveedor_nombre_comercial",
    "proveedor_razon_social_legal",
    "proveedor_tax_id",
    "proveedor_registro",
    "cliente_nombre",
    "cliente_tax_id",
    "numero_factura",
    "fecha_emision",
    "fecha_vencimiento_texto",
    "fecha_vencimiento_calculada",
    "periodo_servicio_desde",
    "periodo_servicio_hasta",
    "moneda",
    "importe_neto",
    "tipo_iva",
    "importe_iva",
    "importe_total",
    "tratamiento_iva",
    "metodo_pago",
    "iban",
    "iban_enmascarado",
    "bic",
    "po_reference",
    "project_reference",
    "condiciones_pago",
    "campos_ilegibles",
]

# Tipo logico por campo, para normalizar y comparar.
FIELD_KINDS = {
    "document_type": "enum",
    "proveedor_nombre_comercial": "str",
    "proveedor_razon_social_legal": "str",
    "proveedor_tax_id": "id",
    "proveedor_registro": "id",
    "cliente_nombre": "str",
    "cliente_tax_id": "id",
    "numero_factura": "id",
    "fecha_emision": "date",
    "fecha_vencimiento_texto": "text_raw",
    "fecha_vencimiento_calculada": "date",
    "periodo_servicio_desde": "date",
    "periodo_servicio_hasta": "date",
    "moneda": "id",
    "importe_neto": "amount",
    "tipo_iva": "amount",
    "importe_iva": "amount",
    "importe_total": "amount",
    "tratamiento_iva": "enum",
    "metodo_pago": "enum",
    "iban": "id",
    "iban_enmascarado": "bool",
    "bic": "id",
    "po_reference": "id",
    "project_reference": "id",
    "condiciones_pago": "text_raw",
    "campos_ilegibles": "list",
}

ENUM_VALUES = {
    "document_type": DOCUMENT_TYPES,
    "tratamiento_iva": TRATAMIENTOS_IVA,
    "metodo_pago": METODOS_PAGO,
}

# Instrucciones por campo: alimentan el prompt y el labels_template.
FIELD_NOTES = {
    "document_type": (
        "PRIMERO clasificar: 'invoice' = factura fiscal (tiene numero de "
        "factura y el IVA esta tratado, desglosado o con regla explicita); "
        "'proforma_or_advance_request' = presupuesto / solicitud de anticipo "
        "(sin numero fiscal, sin IVA desglosado, suele mencionar una factura "
        "final futura); 'other' = cualquier otro documento (OC, recibo, nota)."),
    "proveedor_nombre_comercial": "Nombre comercial del proveedor tal como aparece.",
    "proveedor_razon_social_legal": (
        "Razon social legal (SL, SLU, SA, BV, SAS...). null si no esta clara: "
        "NUNCA inventarla ni derivarla del nombre comercial."),
    "proveedor_tax_id": "CIF / NIF / TIN / VAT del proveedor. null si no aparece.",
    "proveedor_registro": ("Registro mercantil u otro registro (ej. KVK holandes, "
                           "RCS frances). null si no aparece."),
    "cliente_nombre": "Nombre del cliente facturado tal como aparece.",
    "cliente_tax_id": "CIF / NIF / VAT del cliente. null si no aparece.",
    "numero_factura": "Numero fiscal de la factura. null en proformas/anticipos.",
    "fecha_emision": "Fecha de emision, formato ISO YYYY-MM-DD.",
    "fecha_vencimiento_texto": (
        "El texto CRUDO de vencimiento tal como figura (ej. '45 days end of "
        "month', 'al inicio del estudio', '30 dias f.f.'). null si no hay."),
    "fecha_vencimiento_calculada": (
        "Fecha ISO calculada desde fecha_vencimiento_texto + fecha_emision. "
        "Ej.: '45 days end of month' = fin del mes de emision + 45 dias. "
        "null si el texto no es calculable (ej. 'al inicio del estudio')."),
    "periodo_servicio_desde": (
        "Inicio ISO del periodo de servicio si el documento lo indica. "
        "Estructurar menciones tipo 'cuota ABRIL 2026' como 2026-04-01."),
    "periodo_servicio_hasta": (
        "Fin ISO del periodo de servicio. 'cuota ABRIL 2026' -> 2026-04-30."),
    "moneda": "Codigo ISO de la moneda (EUR, USD, GBP...).",
    "importe_neto": "Base imponible / importe sin IVA. null si no esta desglosado.",
    "tipo_iva": "Tipo de IVA en porcentaje (21, 10, 0...). null si no aparece.",
    "importe_iva": "Importe del IVA. null si no esta desglosado.",
    "importe_total": "Importe total del documento.",
    "tratamiento_iva": (
        "'nacional' = IVA espanol desglosado; "
        "'intracomunitario_inversion_sujeto_pasivo' = reverse charge UE "
        "(VAT 0% con mencion a inversion del sujeto pasivo / reverse charge); "
        "'no_desglosado' = el documento no desglosa IVA (tipico de proformas); "
        "'exento_otro' = exencion u otro regimen explicito."),
    "metodo_pago": (
        "'transferencia' | 'domiciliacion_direct_debit' (SEPA direct debit, "
        "cargo en cuenta, mandato) | 'tarjeta' | 'no_indicado'. Solo por "
        "menciones explicitas del documento."),
    "iban": (
        "IBAN de cobro COMPLETO si esta visible. Si viene enmascarado "
        "(asteriscos/puntos), capturar exactamente los digitos visibles con "
        "las mascaras tal cual y marcar iban_enmascarado=true."),
    "iban_enmascarado": "true si el IBAN aparece enmascarado; false si esta completo o no hay IBAN.",
    "bic": "BIC / SWIFT si aparece. null si no.",
    "po_reference": (
        "SOLO si el documento referencia algo etiquetado explicitamente como "
        "PO / OC / Orden de Compra / Purchase Order / Pedido. null si no. "
        "Una referencia sin esa etiqueta NO es po_reference."),
    "project_reference": (
        "Referencias de contrato/proyecto/orden interna (ej. 'ORD-xxx', codigo "
        "de estudio) que NO estan etiquetadas como PO."),
    "condiciones_pago": "Condiciones de pago tal como figuran (texto crudo). null si no hay.",
    "campos_ilegibles": (
        "Lista de nombres de campo que ESTAN en el documento pero resultan "
        "ilegibles (borrosos, cortados). El campo correspondiente va null."),
}

assert set(FIELD_ORDER) == set(FIELD_KINDS) == set(FIELD_NOTES), \
    "esquema inconsistente: FIELD_ORDER, FIELD_KINDS y FIELD_NOTES deben coincidir"


def empty_document() -> dict:
    """Documento con todos los campos en null (y defaults de bool/list)."""
    doc = {f: None for f in FIELD_ORDER}
    doc["iban_enmascarado"] = False
    doc["campos_ilegibles"] = []
    return doc


def validate_document(doc: dict) -> list[str]:
    """Errores de esquema: claves faltantes/sobrantes, enums invalidos, tipos."""
    errors: list[str] = []
    missing = [f for f in FIELD_ORDER if f not in doc]
    extra = [k for k in doc if k not in FIELD_ORDER and k not in ("doc_id", "archivo")]
    if missing:
        errors.append(f"faltan campos: {missing}")
    if extra:
        errors.append(f"campos fuera de esquema: {extra}")
    for field, allowed in ENUM_VALUES.items():
        v = doc.get(field)
        if v is not None and v not in allowed:
            errors.append(f"{field}: valor '{v}' fuera de {allowed}")
    if not isinstance(doc.get("iban_enmascarado"), bool):
        errors.append("iban_enmascarado debe ser booleano")
    if not isinstance(doc.get("campos_ilegibles"), list):
        errors.append("campos_ilegibles debe ser lista")
    return errors


# ------------------------------------------------- vencimiento calculable
_RE_DAYS = re.compile(r"^\s*(\d{1,3})\s*(?:days?|d[ií]as?)\s*$", re.IGNORECASE)
_RE_DAYS_EOM = re.compile(
    r"^\s*(\d{1,3})\s*(?:days?|d[ií]as?)\s*(?:end of month|fin de mes|f\.?\s*d?\.?\s*m\.?|f\.?f\.?)\s*$",
    re.IGNORECASE)


def _end_of_month(d: date) -> date:
    nxt = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return nxt - timedelta(days=1)


def compute_due_date(texto: str | None, fecha_emision: date | None) -> date | None:
    """Implementacion de referencia de fecha_vencimiento_calculada.

    Reglas documentadas:
      'N days' / 'N dias'                      -> emision + N dias
      'N days end of month' / 'N dias fin de
       mes' / 'N dias f.f.' (fecha factura fin
       de mes)                                 -> fin del mes de emision + N dias
      cualquier otro texto ('al inicio del
      estudio', 'a convenir')                  -> None (no calculable)
    """
    if not texto or fecha_emision is None:
        return None
    m = _RE_DAYS_EOM.match(texto)
    if m:
        return _end_of_month(fecha_emision) + timedelta(days=int(m.group(1)))
    m = _RE_DAYS.match(texto)
    if m:
        return fecha_emision + timedelta(days=int(m.group(1)))
    return None
