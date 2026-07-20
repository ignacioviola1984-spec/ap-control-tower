"""Autenticacion WSAA de ARCA: firma del TRA y cache del ticket de acceso.

Flujo (manual WSAA, verificado 2026-07-20, ver runbook): se construye un
``loginTicketRequest`` (TRA), se firma como CMS/PKCS#7 con el certificado
X.509 emitido por ARCA, y se envia al endpoint ``LoginCms``. El ticket
devuelto (token + sign) dura ~12 horas y se cachea en disco; se renueva solo
cuando esta por vencer. Un ticket vencido o ausente NUNCA bloquea el pipeline:
el que llama degrada a la advertencia "verificacion no disponible".

Credenciales SOLO por variables de entorno (rutas a archivos; en produccion,
montadas desde Secret Manager). Nada de certificados ni claves en el repo.

    AP_ARCA_ENV          homologacion | produccion (default homologacion)
    AP_ARCA_CERT_PATH    ruta al certificado X.509 (PEM)
    AP_ARCA_KEY_PATH     ruta a la clave privada (PEM)
    AP_ARCA_TICKET_DIR   cache de tickets (default ~/.ap_control_tower/wsaa)

La firma CMS usa ``cryptography`` (requirements-arca.txt); el import es
perezoso para que la imagen de la demo no lo necesite.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOGIN_URLS = {
    "homologacion": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
    "produccion": "https://wsaa.afip.gov.ar/ws/services/LoginCms",
}
TICKET_TTL_HOURS = 12
# Margen antes del vencimiento a partir del cual se renueva el ticket.
RENEW_MARGIN_MINUTES = 30
REQUEST_TIMEOUT_SECONDS = 30


class WsaaError(RuntimeError):
    """Fallo de autenticacion WSAA (certificado, red o respuesta invalida)."""


@dataclass(frozen=True)
class WsaaConfig:
    environment: str = "homologacion"
    cert_path: str | None = None
    key_path: str | None = None
    ticket_dir: str | None = None

    @classmethod
    def from_env(cls) -> "WsaaConfig":
        return cls(
            environment=os.getenv("AP_ARCA_ENV", "homologacion").strip().lower(),
            cert_path=os.getenv("AP_ARCA_CERT_PATH") or None,
            key_path=os.getenv("AP_ARCA_KEY_PATH") or None,
            ticket_dir=os.getenv("AP_ARCA_TICKET_DIR") or None,
        )

    @property
    def login_url(self) -> str:
        try:
            return LOGIN_URLS[self.environment]
        except KeyError:
            raise WsaaError(f"AP_ARCA_ENV invalido: {self.environment!r} "
                            "(homologacion | produccion)") from None

    @property
    def configured(self) -> bool:
        """True si hay certificado y clave apuntados y existentes."""
        return bool(self.cert_path and self.key_path
                    and Path(self.cert_path).is_file()
                    and Path(self.key_path).is_file())

    def cache_path(self, service: str) -> Path:
        base = Path(self.ticket_dir) if self.ticket_dir else (
            Path.home() / ".ap_control_tower" / "wsaa")
        return base / f"ta_{self.environment}_{service}.json"


@dataclass(frozen=True)
class Ticket:
    token: str
    sign: str
    expiration: str  # ISO 8601 con zona, tal como lo emite WSAA

    def expires_soon(self, now: datetime | None = None,
                     margin_minutes: int = RENEW_MARGIN_MINUTES) -> bool:
        moment = now or datetime.now(timezone.utc)
        try:
            expiry = datetime.fromisoformat(self.expiration)
        except ValueError:
            return True
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return moment >= expiry - timedelta(minutes=margin_minutes)


# ------------------------------------------------------------------ TRA + CMS
def build_tra(service: str, now: datetime | None = None,
              ttl_hours: int = TICKET_TTL_HOURS) -> bytes:
    """loginTicketRequest XML (TRA) para el servicio dado."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    unique_id = uuid.uuid4().int % (2**31)
    generation = moment - timedelta(minutes=5)   # tolerancia de reloj
    expiration = moment + timedelta(hours=ttl_hours)
    fmt = "%Y-%m-%dT%H:%M:%S%z"

    def _iso(dt: datetime) -> str:
        raw = dt.strftime(fmt)
        return raw[:-2] + ":" + raw[-2:]  # +0000 -> +00:00

    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<loginTicketRequest version=\"1.0\">"
        "<header>"
        f"<uniqueId>{unique_id}</uniqueId>"
        f"<generationTime>{_iso(generation)}</generationTime>"
        f"<expirationTime>{_iso(expiration)}</expirationTime>"
        "</header>"
        f"<service>{service}</service>"
        "</loginTicketRequest>"
    ).encode("utf-8")


def sign_tra_cms(tra: bytes, cert_pem: bytes, key_pem: bytes) -> str:
    """Firma CMS/PKCS#7 del TRA, en base64 (payload de LoginCms)."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.serialization import pkcs7
        from cryptography.x509 import load_pem_x509_certificate
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise WsaaError(
            "Falta la libreria 'cryptography' (instalar requirements-arca.txt)"
        ) from exc

    certificate = load_pem_x509_certificate(cert_pem)
    private_key = serialization.load_pem_private_key(key_pem, password=None)
    cms_der = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(tra)
        .add_signer(certificate, private_key, hashes.SHA256())
        .sign(serialization.Encoding.DER, [])
    )
    return base64.b64encode(cms_der).decode("ascii")


# ------------------------------------------------------------------ LoginCms
def _soap_login_request(cms_b64: str) -> bytes:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<soapenv:Envelope "
        "xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:wsaa=\"http://wsaa.view.sua.dvadac.desein.afip.gov\">"
        "<soapenv:Header/><soapenv:Body>"
        f"<wsaa:loginCms><wsaa:in0>{cms_b64}</wsaa:in0></wsaa:loginCms>"
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


def _primer_elemento(root: ET.Element, localname: str) -> ET.Element | None:
    """Primer elemento con ese nombre local, ignorando namespaces."""
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == localname:
            return element
    return None


def parse_login_response(payload: bytes) -> Ticket:
    """Extrae token/sign/expiration del SOAP de LoginCms."""
    try:
        envelope = ET.fromstring(payload)
        retorno = _primer_elemento(envelope, "loginCmsReturn")
        if retorno is None or not (retorno.text or "").strip():
            raise WsaaError("Respuesta WSAA sin loginCmsReturn")
        ta = ET.fromstring(retorno.text)
        token = ta.findtext(".//token") or ""
        sign = ta.findtext(".//sign") or ""
        expiration = ta.findtext(".//expirationTime") or ""
    except ET.ParseError as exc:
        raise WsaaError(f"Respuesta WSAA invalida: {exc}") from exc
    if not (token and sign and expiration):
        raise WsaaError("Respuesta WSAA incompleta (sin token/sign/expiration)")
    return Ticket(token=token, sign=sign, expiration=expiration)


def request_ticket(config: WsaaConfig, service: str,
                   transport=None, now: datetime | None = None) -> Ticket:
    """Solicita un ticket nuevo a WSAA (1 llamada de red)."""
    if not config.configured:
        raise WsaaError(
            "Certificado WSAA no configurado (AP_ARCA_CERT_PATH / "
            "AP_ARCA_KEY_PATH); ver runbook_controles_arca.md")
    cert_pem = Path(config.cert_path).read_bytes()
    key_pem = Path(config.key_path).read_bytes()
    cms_b64 = sign_tra_cms(build_tra(service, now=now), cert_pem, key_pem)
    send = transport or _default_transport
    try:
        payload = send(config.login_url, _soap_login_request(cms_b64))
    except WsaaError:
        raise
    except Exception as exc:
        raise WsaaError(f"WSAA inalcanzable: {exc}") from exc
    return parse_login_response(payload)


def get_ticket(config: WsaaConfig, service: str,
               transport=None, now: datetime | None = None) -> Ticket:
    """Ticket vigente para el servicio: usa el cache en disco y renueva solo
    cuando esta por vencer. La escritura del cache es atomica (rename)."""
    cache = config.cache_path(service)
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            cached = Ticket(**{k: data[k] for k in ("token", "sign", "expiration")})
            if not cached.expires_soon(now=now):
                return cached
        except (ValueError, KeyError, TypeError):
            pass  # cache corrupto: se renueva
    ticket = request_ticket(config, service, transport=transport, now=now)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporal = cache.with_suffix(".tmp")
    temporal.write_text(json.dumps({
        "token": ticket.token, "sign": ticket.sign,
        "expiration": ticket.expiration}, ensure_ascii=False), encoding="utf-8")
    temporal.replace(cache)
    return ticket
