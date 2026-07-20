"""Validadores puros de los controles ARCA. Nada de I/O aca.

Reciben datos del documento + datos de padron/APOC ya resueltos y devuelven
senales tipadas. La semantica calza con la politica canonica de
``ui/trial/workflow.py``: una senal bloqueante se agrega a ``result.warnings``
y deriva a revision humana por el mecanismo existente; una senal FYI se
muestra y audita sin derivar (via ``FYI_WARNINGS``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cuit import cuit_valido, es_cuit_candidato

C10_PADRON = "C10_PADRON"
C11_APOC = "C11_APOC"

BLOQUEANTE = "bloqueante"
ADVERTENCIA = "advertencia"
FYI = "fyi"

# Sufijo que convierte la advertencia en FYI para la politica de derivacion
# (AP_ARCA_FAIL_MODE=warn). workflow.FYI_WARNINGS filtra por este texto.
SUFIJO_SOLO_AVISO = " (modo aviso: no deriva)"

MOTIVO_DV_INVALIDO = "CUIT del proveedor con dígito verificador inválido"
MOTIVO_INEXISTENTE = "CUIT del proveedor inexistente en el padrón de ARCA"
MOTIVO_APOC = "proveedor incluido en la base de facturas apócrifas de ARCA (APOC)"
MOTIVO_NO_DISPONIBLE = ("verificación contra padrón ARCA no disponible: "
                        "documento no verificado")


@dataclass(frozen=True)
class Senal:
    control: str            # C10_PADRON | C11_APOC
    tipo: str               # bloqueante | advertencia | fyi
    codigo: str             # identificador estable para auditoria
    motivo: str             # texto visible en la cola, en español
    severidad: str = "alta"  # "maxima" solo para APOC
    evidencia: dict = field(default_factory=dict)


def _tax_id(document: dict) -> str | None:
    valor = document.get("proveedor_tax_id")
    return None if valor is None else str(valor)


def letra_comprobante(document: dict) -> str | None:
    """Letra del comprobante argentino (A/B/C/E/M) si es inequivoca.

    El esquema de extraccion no tiene un campo dedicado: se acepta un campo
    explicito ``tipo_comprobante`` (fixtures/futuras fuentes) o un numero de
    factura con la letra adelante ("A 0001-00001234", "FC-A-0001-...").
    Ante ambiguedad devuelve None: este control prefiere no senalar a
    senalar mal.
    """
    explicito = document.get("tipo_comprobante")
    if explicito:
        texto = str(explicito).strip().upper()
        match = re.fullmatch(r"(?:FACTURA\s+)?([ABCEM])", texto)
        if match:
            return match.group(1)
    numero = str(document.get("numero_factura") or "").strip().upper()
    match = re.match(r"^(?:FC?[- ])?([ABCEM])[- ]\d", numero)
    if match:
        return match.group(1)
    return None


def validar_cuit_local(document: dict) -> list[Senal]:
    """Digito verificador (mod 11). Corre en TODOS los modos, sin red.

    Solo evalua candidatos a CUIT (11 digitos): un CIF europeo o un tax id
    enmascarado jamas genera senal.
    """
    valor = _tax_id(document)
    if valor is None or not es_cuit_candidato(valor):
        return []
    if cuit_valido(valor):
        return []
    return [Senal(control=C10_PADRON, tipo=BLOQUEANTE, codigo="cuit_dv_invalido",
                  motivo=MOTIVO_DV_INVALIDO,
                  evidencia={"proveedor_tax_id": valor})]


def validar_padron(document: dict, persona: dict | None) -> list[Senal]:
    """Senales de C10 a partir de la constancia YA resuelta (o None si el
    padron no se consulto: en ese caso no hay nada que validar aca)."""
    if persona is None:
        return []
    senales: list[Senal] = []
    evidencia = {"proveedor_tax_id": _tax_id(document),
                 "padron_fetched_at": persona.get("fetched_at")}
    if not persona.get("existe", False):
        return [Senal(control=C10_PADRON, tipo=BLOQUEANTE,
                      codigo="cuit_inexistente", motivo=MOTIVO_INEXISTENTE,
                      evidencia=evidencia)]
    estado = (persona.get("estado") or "").upper()
    if estado and estado != "ACTIVO":
        senales.append(Senal(
            control=C10_PADRON, tipo=BLOQUEANTE, codigo="cuit_no_activo",
            motivo=("CUIT del proveedor no activo en el padrón de ARCA "
                    f"(estado: {estado})"),
            evidencia={**evidencia, "estado": estado}))
    letra = letra_comprobante(document)
    condicion = persona.get("condicion_iva")
    if letra == "A" and condicion == "monotributo":
        senales.append(Senal(
            control=C10_PADRON, tipo=BLOQUEANTE,
            codigo="condicion_incoherente",
            motivo=("condición fiscal del proveedor (monotributo) incoherente "
                    "con el tipo de comprobante (factura A)"),
            evidencia={**evidencia, "condicion_iva": condicion,
                       "tipo_comprobante": letra}))
    return senales


def validar_apoc(document: dict, en_apoc: bool,
                 version_info: dict | None = None) -> list[Senal]:
    """C11: presencia en la base APOC local. Bloqueante de maxima severidad:
    jamas se auto-aprueba, siempre a revision humana con el motivo visible."""
    if not en_apoc:
        return []
    evidencia = {"proveedor_tax_id": _tax_id(document)}
    if version_info:
        evidencia["apoc_version_id"] = version_info.get("version_id")
        evidencia["apoc_checksum"] = version_info.get("checksum")
        evidencia["apoc_fecha_descarga"] = version_info.get("fecha_descarga")
    return [Senal(control=C11_APOC, tipo=BLOQUEANTE, codigo="proveedor_en_apoc",
                  motivo=MOTIVO_APOC, severidad="maxima", evidencia=evidencia)]


def senal_no_disponible(detalle: str, fail_mode: str = "derive") -> Senal:
    """Padron caido / timeout / sin certificado: advertencia explicita.

    ``derive`` (default): la advertencia deriva a revision humana.
    ``warn``: se muestra y audita pero no deriva (sufijo FYI). En ningun caso
    hay pase silencioso: la falta de verificacion siempre deja rastro.
    """
    if fail_mode == "warn":
        return Senal(control=C10_PADRON, tipo=FYI, codigo="padron_no_disponible",
                     motivo=MOTIVO_NO_DISPONIBLE + SUFIJO_SOLO_AVISO,
                     evidencia={"detalle": detalle[:200]})
    return Senal(control=C10_PADRON, tipo=ADVERTENCIA,
                 codigo="padron_no_disponible", motivo=MOTIVO_NO_DISPONIBLE,
                 evidencia={"detalle": detalle[:200]})


def advertencia_apoc_desactualizada(version_info: dict | None) -> str | None:
    """Advertencia GLOBAL de la corrida (no por documento)."""
    if not version_info or not version_info.get("desactualizada"):
        return None
    fecha = str(version_info.get("fecha_descarga") or "")[:10]
    return f"base APOC desactualizada, última descarga: {fecha}"
