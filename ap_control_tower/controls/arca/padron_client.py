"""Cliente del padron de ARCA (constancia de inscripcion) con cache local.

Servicio verificado 2026-07-20 (manual v4.1, ver runbook):
``ws_sr_constancia_inscripcion`` (``ws_sr_padron_a5`` esta deprecado), metodo
``getPersona_v2``, autenticado con ticket WSAA del mismo servicio.

Reglas de operacion:
  * timeout corto (5 s) y 2 reintentos: el pipeline no se bloquea por ARCA;
  * 1 llamada de red por CUIT nuevo cada TTL (``AP_ARCA_PADRON_TTL_DIAS``,
    default 7 dias), NUNCA por factura: el resto sale de ``arca_padron_cache``;
  * si el servicio no responde se levanta ``PadronNoDisponible`` y el que
    llama emite la advertencia explicita (prohibido el pase silencioso).
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from .cuit import normalizar
from .wsaa import WsaaConfig, WsaaError, get_ticket

SERVICE_NAME = "ws_sr_constancia_inscripcion"
ENDPOINTS = {
    "homologacion": "https://awshomo.arca.gob.ar/sr-padron/webservices/personaServiceA5",
    "produccion": "https://aws.arca.gob.ar/sr-padron/webservices/personaServiceA5",
}
REQUEST_TIMEOUT_SECONDS = 5
MAX_RETRIES = 2
DEFAULT_TTL_DIAS = 7


class PadronNoDisponible(RuntimeError):
    """El padron no pudo consultarse (red, WSAA o certificado ausente)."""


def ttl_dias() -> int:
    try:
        return max(0, int(os.getenv("AP_ARCA_PADRON_TTL_DIAS", DEFAULT_TTL_DIAS)))
    except ValueError:
        return DEFAULT_TTL_DIAS


def _primer_elemento(root: ET.Element, localname: str) -> ET.Element | None:
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == localname:
            return element
    return None


def _texto_local(root: ET.Element, localname: str) -> str | None:
    element = _primer_elemento(root, localname)
    if element is None:
        return None
    return (element.text or "").strip() or None


def _soap_get_persona(token: str, sign: str, cuit_representada: str,
                      cuit: str) -> bytes:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<soapenv:Envelope "
        "xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:a5=\"http://a5.soap.ws.server.puc.sr/\">"
        "<soapenv:Header/><soapenv:Body>"
        "<a5:getPersona_v2>"
        f"<token>{token}</token><sign>{sign}</sign>"
        f"<cuitRepresentada>{cuit_representada}</cuitRepresentada>"
        f"<idPersona>{cuit}</idPersona>"
        "</a5:getPersona_v2>"
        "</soapenv:Body></soapenv:Envelope>"
    ).encode("utf-8")


def _default_transport(url: str, body: bytes) -> bytes:
    from urllib.request import Request, urlopen

    request = Request(url, data=body, headers={
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "",
    })
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read()


def parse_persona_response(payload: bytes) -> dict:
    """Normaliza la respuesta de getPersona_v2 a un payload chico y estable.

    Claves: existe, estado, condicion_iva, razon_social, error. Nunca se
    persiste el XML crudo.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise PadronNoDisponible(f"respuesta del padron ilegible: {exc}") from exc

    fault = _primer_elemento(root, "Fault")
    if fault is not None:
        detalle = _texto_local(fault, "faultstring") or "SOAP Fault"
        if "no existe" in detalle.lower():
            return {"existe": False, "estado": None, "condicion_iva": None,
                    "razon_social": None, "error": detalle}
        raise PadronNoDisponible(f"padron devolvio error: {detalle}")

    error_constancia = _texto_local(root, "errorConstancia")
    persona = _primer_elemento(root, "datosGenerales")
    if persona is None:
        detalle = error_constancia or _texto_local(root, "error")
        if detalle and "no existe" in detalle.lower():
            return {"existe": False, "estado": None, "condicion_iva": None,
                    "razon_social": None, "error": detalle}
        raise PadronNoDisponible(
            f"respuesta del padron sin datosGenerales: {detalle or 'desconocido'}")

    estado = (_texto_local(persona, "estadoClave") or "").upper() or None
    razon = _texto_local(persona, "razonSocial")
    if razon is None:
        apellido = _texto_local(persona, "apellido") or ""
        nombre = _texto_local(persona, "nombre") or ""
        razon = " ".join(p for p in (apellido, nombre) if p) or None

    if _primer_elemento(root, "datosMonotributo") is not None:
        condicion = "monotributo"
    elif _primer_elemento(root, "datosRegimenGeneral") is not None:
        condicion = "responsable_inscripto"
    else:
        condicion = "sin_regimen_informado"

    return {"existe": True, "estado": estado, "condicion_iva": condicion,
            "razon_social": razon, "error": None}


class PadronClient:
    """Cliente minimo de getPersona_v2 con WSAA, timeout corto y retries."""

    def __init__(self, config: WsaaConfig | None = None,
                 cuit_representada: str | None = None,
                 transport=None, retries: int = MAX_RETRIES):
        self.config = config or WsaaConfig.from_env()
        self.cuit_representada = (
            cuit_representada or os.getenv("AP_ARCA_CUIT_REPRESENTADA") or "")
        self.transport = transport or _default_transport
        self.retries = retries

    @property
    def endpoint(self) -> str:
        try:
            return ENDPOINTS[self.config.environment]
        except KeyError:
            raise PadronNoDisponible(
                f"AP_ARCA_ENV invalido: {self.config.environment!r}") from None

    def get_persona(self, cuit) -> dict:
        limpio = normalizar(cuit)
        if limpio is None:
            raise ValueError(f"no es un CUIT: {cuit!r}")
        representada = normalizar(self.cuit_representada)
        if representada is None:
            raise PadronNoDisponible(
                "falta AP_ARCA_CUIT_REPRESENTADA (CUIT titular del certificado)")
        try:
            ticket = get_ticket(self.config, SERVICE_NAME)
        except WsaaError as exc:
            raise PadronNoDisponible(str(exc)) from exc
        body = _soap_get_persona(ticket.token, ticket.sign, representada, limpio)
        ultimo_error: Exception | None = None
        for _ in range(1 + max(0, self.retries)):
            try:
                return parse_persona_response(self.transport(self.endpoint, body))
            except PadronNoDisponible as exc:
                ultimo_error = exc
            except Exception as exc:  # timeout / red
                ultimo_error = exc
        raise PadronNoDisponible(f"padron inalcanzable: {ultimo_error}")


# ------------------------------------------------------------------ cache
def cached_persona(db, client: PadronClient, cuit,
                   now: datetime | None = None) -> dict:
    """Constancia desde ``arca_padron_cache`` si esta vigente; si no, consulta
    y actualiza el cache. Devuelve el payload normalizado + ``fetched_at``."""
    from sqlalchemy import select

    from ...persistence.models_sql import ArcaPadronCache

    limpio = normalizar(cuit)
    if limpio is None:
        raise ValueError(f"no es un CUIT: {cuit!r}")
    moment = now or datetime.now(timezone.utc)
    fila = db.execute(
        select(ArcaPadronCache).where(ArcaPadronCache.cuit == limpio)
    ).scalar_one_or_none()
    if fila is not None:
        fetched = fila.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if moment - fetched <= timedelta(days=ttl_dias()):
            return {**fila.payload, "fetched_at": fetched.isoformat(timespec="seconds")}

    payload = client.get_persona(limpio)
    if fila is None:
        fila = ArcaPadronCache(cuit=limpio)
        db.add(fila)
    fila.payload = payload
    fila.estado = payload.get("estado")
    fila.condicion_iva = payload.get("condicion_iva")
    fila.fetched_at = moment
    db.flush()
    return {**payload, "fetched_at": moment.isoformat(timespec="seconds")}
