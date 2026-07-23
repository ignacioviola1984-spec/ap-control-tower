"""Cliente Gmail READ-ONLY (OAuth refresh token, scope gmail.readonly).

Solo llama a endpoints de lectura: labels.list, messages.list, messages.get y
messages.attachments.get. No existe ningun metodo de escritura (enviar, borrar,
archivar, modificar etiquetas): esa es la garantia del MVP.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field

# Scope UNICO de solo lectura. La demo y el trial no piden nada mas.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Variables de entorno (nunca en Git ni en la imagen).
ENV_USER = "AP_GMAIL_USER"
ENV_LABEL = "AP_GMAIL_LABEL"
ENV_CLIENT_ID = "AP_GMAIL_CLIENT_ID"
ENV_CLIENT_SECRET = "AP_GMAIL_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "AP_GMAIL_REFRESH_TOKEN"

DEFAULT_USER = "mberhensen@bmcinnovation.com"
DEFAULT_LABEL = "AP-DEMO"


@dataclass(frozen=True)
class GmailConfig:
    user: str
    label: str
    client_id: str
    client_secret: str
    refresh_token: str

    @classmethod
    def from_env(cls) -> "GmailConfig | None":
        client_id = os.environ.get(ENV_CLIENT_ID)
        client_secret = os.environ.get(ENV_CLIENT_SECRET)
        refresh_token = os.environ.get(ENV_REFRESH_TOKEN)
        if not (client_id and client_secret and refresh_token):
            return None
        return cls(
            user=os.environ.get(ENV_USER, DEFAULT_USER),
            label=os.environ.get(ENV_LABEL, DEFAULT_LABEL),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )


def gmail_configured() -> bool:
    return GmailConfig.from_env() is not None


def mailbox_configured() -> bool:
    """True si hay un buzón IMAP o Gmail API configurado."""
    from .imap_client import imap_configured
    return imap_configured() or gmail_configured()


def mailbox_provider() -> str | None:
    from .imap_client import imap_configured
    if imap_configured():
        return "IMAP"
    if gmail_configured():
        return "Gmail"
    return None


def mailbox_address() -> str:
    """Dirección asignada al circuito AP, aun si faltan credenciales de lectura."""
    return (
        os.environ.get("AP_IMAP_USER")
        or os.environ.get(ENV_USER)
        or DEFAULT_USER
    ).strip()


@dataclass(frozen=True)
class GmailAttachment:
    message_id: str
    attachment_id: str
    filename: str
    mime_type: str
    size: int = 0


@dataclass
class GmailMessage:
    id: str
    sender: str
    subject: str
    date: str
    attachments: list = field(default_factory=list)   # list[GmailAttachment]


def _is_pdf(filename: str, mime_type: str) -> bool:
    return (filename or "").lower().endswith(".pdf") or mime_type == "application/pdf"


def _walk_pdf_attachments(payload: dict, message_id: str) -> list:
    """Recorre las partes del mensaje y junta SOLO los adjuntos PDF."""
    found: list = []
    filename = payload.get("filename") or ""
    mime = payload.get("mimeType") or ""
    body = payload.get("body") or {}
    if filename and _is_pdf(filename, mime) and body.get("attachmentId"):
        found.append(GmailAttachment(
            message_id=message_id,
            attachment_id=body["attachmentId"],
            filename=filename,
            mime_type=mime or "application/pdf",
            size=int(body.get("size") or 0),
        ))
    for part in payload.get("parts") or ():
        found.extend(_walk_pdf_attachments(part, message_id))
    return found


class RealGmailClient:
    """Cliente Gmail de solo lectura. Construye credenciales desde el refresh
    token OAuth y consulta la API. No expone ninguna operacion de escritura."""

    def __init__(self, config: GmailConfig) -> None:
        self._config = config
        self._service = None

    def _build_service(self):
        if self._service is not None:
            return self._service
        from google.oauth2.credentials import Credentials  # type: ignore
        from googleapiclient.discovery import build  # type: ignore

        creds = Credentials(
            token=None,
            refresh_token=self._config.refresh_token,
            client_id=self._config.client_id,
            client_secret=self._config.client_secret,
            token_uri=_TOKEN_URI,
            scopes=SCOPES,
        )
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def _label_id(self, service) -> str | None:
        labels = service.users().labels().list(
            userId=self._config.user).execute().get("labels", [])
        for label in labels:
            if label.get("name") == self._config.label:
                return label.get("id")
        return None

    def list_messages(self, max_results: int = 50) -> list:
        service = self._build_service()
        label_id = self._label_id(service)
        if label_id is None:
            return []
        resp = service.users().messages().list(
            userId=self._config.user, labelIds=[label_id],
            maxResults=max_results).execute()
        out: list = []
        for meta in resp.get("messages", []) or ():
            full = service.users().messages().get(
                userId=self._config.user, id=meta["id"], format="full").execute()
            payload = full.get("payload", {}) or {}
            headers = {h.get("name", "").lower(): h.get("value", "")
                       for h in payload.get("headers", []) or ()}
            out.append(GmailMessage(
                id=meta["id"],
                sender=headers.get("from", ""),
                subject=headers.get("subject", ""),
                date=headers.get("date", ""),
                attachments=_walk_pdf_attachments(payload, meta["id"]),
            ))
        return out

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        service = self._build_service()
        att = service.users().messages().attachments().get(
            userId=self._config.user, messageId=message_id,
            id=attachment_id).execute()
        return base64.urlsafe_b64decode(att["data"])


def build_client():
    """Prioriza el buzón IMAP externo; conserva Gmail como fallback."""
    from .imap_client import build_imap_client
    imap_client = build_imap_client()
    if imap_client is not None:
        return imap_client
    config = GmailConfig.from_env()
    return RealGmailClient(config) if config is not None else None
