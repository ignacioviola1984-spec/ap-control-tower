"""Orquestacion de los controles ARCA por documento (modos off | mock | live).

Punto unico que llama la ingesta del Trial: resuelve el modo, junta los datos
(padron cacheado, APOC local o fixtures mock), corre los validadores puros y
devuelve las senales + advertencias globales. El llamador agrega los motivos
a ``result.warnings`` (mecanismo canonico de derivacion) y audita.

    AP_ARCA_MODE       off | mock | live   (default: mock; fixtures vacias =
                       cero senales de padron/APOC; el CUIT local corre SIEMPRE)
    AP_ARCA_FAIL_MODE  warn | derive       (default: derive)

En ``live`` la base local (AP_DATABASE_URL) y el certificado WSAA son
obligatorios; si faltan, cada documento recibe la advertencia explicita de
"verificacion no disponible" (prohibido el pase silencioso).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .cuit import es_cuit_candidato, normalizar
from .validators import (
    Senal,
    advertencia_apoc_desactualizada,
    senal_no_disponible,
    validar_apoc,
    validar_cuit_local,
    validar_padron,
)

MODOS = ("off", "mock", "live")


def modo_actual() -> str:
    modo = os.getenv("AP_ARCA_MODE", "mock").strip().lower()
    return modo if modo in MODOS else "mock"


def fail_mode_actual() -> str:
    valor = os.getenv("AP_ARCA_FAIL_MODE", "derive").strip().lower()
    return valor if valor in ("warn", "derive") else "derive"


@dataclass
class MockArcaData:
    """Fixtures locales del modo mock (dev/tests). Vacias por defecto:
    'mock sin matches' se comporta identico a 'off' para el ruteo."""

    apoc: set = field(default_factory=set)                 # CUITs listados
    padron: dict = field(default_factory=dict)             # cuit -> persona dict
    apoc_version: dict | None = None

    def reset(self) -> None:
        self.apoc.clear()
        self.padron.clear()
        self.apoc_version = None


mock_data = MockArcaData()


@dataclass(frozen=True)
class EvaluacionArca:
    modo: str
    senales: list  # list[Senal] por documento
    advertencias_globales: list  # strings de corrida (p. ej. base APOC vieja)


# ------------------------------------------------------------------ acceso live
def _db_session_factory():
    """Session factory perezosa sobre la base configurada; None si no hay."""
    from ...persistence.config import is_persistence_available

    if not is_persistence_available():
        return None
    from ...persistence.session import build_engine, session_scope

    engine = build_engine()
    return lambda: session_scope(engine)


def _evaluar_live(document: dict, fail_mode: str) -> tuple[list, list]:
    from . import apoc_source, padron_client

    senales: list[Senal] = []
    globales: list[str] = []
    factory = _db_session_factory()
    if factory is None:
        senales.append(senal_no_disponible(
            "base local ARCA no configurada (AP_DATABASE_URL)", fail_mode))
        return senales, globales

    tax_id = document.get("proveedor_tax_id")
    with factory() as db:
        version_info = apoc_source.latest_version_info(db)
        if version_info is None:
            senales.append(senal_no_disponible(
                "base APOC local vacia: correr refresh_apoc", fail_mode))
        else:
            stale = advertencia_apoc_desactualizada(version_info)
            if stale:
                globales.append(stale)
            if es_cuit_candidato(tax_id):
                senales.extend(validar_apoc(
                    document, apoc_source.is_listed(db, tax_id), version_info))

        if es_cuit_candidato(tax_id):
            try:
                persona = padron_client.cached_persona(
                    db, padron_client.PadronClient(), tax_id)
                senales.extend(validar_padron(document, persona))
            except padron_client.PadronNoDisponible as exc:
                senales.append(senal_no_disponible(str(exc), fail_mode))
    return senales, globales


def _evaluar_mock(document: dict) -> tuple[list, list]:
    senales: list[Senal] = []
    globales: list[str] = []
    tax_id = document.get("proveedor_tax_id")
    limpio = normalizar(tax_id)
    if limpio is None:
        return senales, globales
    version = mock_data.apoc_version or {"version_id": "mock",
                                         "checksum": "mock", "fecha_descarga": ""}
    senales.extend(validar_apoc(document, limpio in {
        normalizar(c) for c in mock_data.apoc}, version))
    persona = mock_data.padron.get(limpio)
    senales.extend(validar_padron(document, persona))
    stale = advertencia_apoc_desactualizada(mock_data.apoc_version)
    if stale:
        globales.append(stale)
    return senales, globales


# ------------------------------------------------------------------ API publica
def evaluar_documento(document: dict, *, modo: str | None = None,
                      fail_mode: str | None = None) -> EvaluacionArca:
    """Senales ARCA para un documento. Sin UI y sin tocar el documento."""
    modo = modo or modo_actual()
    fail_mode = fail_mode or fail_mode_actual()
    senales = validar_cuit_local(document)  # todos los modos, sin red
    globales: list[str] = []
    if modo == "live":
        extra, globales = _evaluar_live(document, fail_mode)
        senales.extend(extra)
    elif modo == "mock":
        extra, globales = _evaluar_mock(document)
        senales.extend(extra)
    # modo off: padron/APOC se saltean; el llamador deja constancia informativa.
    return EvaluacionArca(modo=modo, senales=senales,
                          advertencias_globales=globales)


def enriquecer_resultado(result, audit=None, *, modo: str | None = None,
                         fail_mode: str | None = None) -> EvaluacionArca:
    """Agrega los motivos ARCA a ``result.warnings`` (los bloqueantes y las
    advertencias derivan por el mecanismo canonico; los FYI quedan visibles
    sin derivar) y registra un evento de auditoria por senal."""
    evaluacion = evaluar_documento(result.document, modo=modo, fail_mode=fail_mode)
    for senal in evaluacion.senales:
        if senal.motivo not in result.warnings:
            result.warnings.append(senal.motivo)
        if audit is not None:
            audit.add(agent="arca", action="control-arca-senal",
                      invoice_id=str(result.doc_id), result=senal.tipo,
                      evidence={"control": senal.control, "codigo": senal.codigo,
                                "severidad": senal.severidad,
                                "modo": evaluacion.modo, **senal.evidencia})
    return evaluacion


def registrar_modo_off(audit) -> None:
    """Constancia informativa (una por ingesta) de que ARCA esta apagado."""
    audit.add(agent="arca", action="controles-arca-omitidos", result="off",
              evidence={"modo": "off",
                        "detalle": "AP_ARCA_MODE=off: padron y APOC no verificados"})
