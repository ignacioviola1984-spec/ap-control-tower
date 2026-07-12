"""Buzon IMAP de solo lectura para proveedores de correo externos.

Usa exclusivamente SELECT readonly, SEARCH y FETCH con BODY.PEEK. No expone
operaciones para enviar, borrar, mover, marcar ni modificar mensajes.
"""

from __future__ import annotations

import imaplib
import os
from dataclasses import dataclass
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser

from .client import GmailAttachment, GmailMessage

ENV_HOST = "AP_IMAP_HOST"
ENV_PORT = "AP_IMAP_PORT"
ENV_USER = "AP_IMAP_USER"
ENV_PASSWORD = "AP_IMAP_PASSWORD"
ENV_FOLDER = "AP_IMAP_FOLDER"

DEFAULT_PORT = 993
DEFAULT_FOLDER = "AP-DEMO"


@dataclass(frozen=True)
class IMAPConfig:
    host: str
    port: int
    user: str
    password: str
    folder: str

    @classmethod
    def from_env(cls) -> "IMAPConfig | None":
        host = os.environ.get(ENV_HOST)
        user = os.environ.get(ENV_USER)
        password = os.environ.get(ENV_PASSWORD)
        if not (host and user and password):
            return None
        return cls(
            host=host,
            port=int(os.environ.get(ENV_PORT, str(DEFAULT_PORT))),
            user=user,
            password=password,
            folder=os.environ.get(ENV_FOLDER, DEFAULT_FOLDER),
        )


def imap_configured() -> bool:
    return IMAPConfig.from_env() is not None


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return value


class ReadOnlyIMAPClient:
    """Adaptador IMAP deliberadamente limitado a lectura."""

    def __init__(self, config: IMAPConfig, connection_factory=None) -> None:
        self._config = config
        self._connection_factory = connection_factory or imaplib.IMAP4_SSL
        self._attachment_cache: dict[tuple[str, str], bytes] = {}

    def _connect(self):
        conn = self._connection_factory(self._config.host, self._config.port)
        conn.login(self._config.user, self._config.password)
        status, _ = conn.select(self._config.folder, readonly=True)
        if status != "OK":
            conn.logout()
            raise RuntimeError(
                f"No se pudo abrir la carpeta IMAP {self._config.folder!r} en solo lectura")
        return conn

    def list_messages(self, max_results: int = 50) -> list:
        conn = self._connect()
        try:
            status, data = conn.uid("search", None, "ALL")
            if status != "OK":
                raise RuntimeError("No se pudo listar el buzón IMAP")
            uids = (data[0] or b"").split()
            selected = list(reversed(uids[-max_results:]))
            messages: list[GmailMessage] = []
            self._attachment_cache.clear()
            for raw_uid in selected:
                uid = raw_uid.decode("ascii")
                status, fetched = conn.uid("fetch", raw_uid, "(BODY.PEEK[])")
                if status != "OK":
                    continue
                raw_message = next(
                    (item[1] for item in fetched
                     if isinstance(item, tuple) and isinstance(item[1], bytes)),
                    None,
                )
                if raw_message is None:
                    continue
                parsed = BytesParser(policy=policy.default).parsebytes(raw_message)
                attachments: list[GmailAttachment] = []
                for index, part in enumerate(parsed.iter_attachments()):
                    filename = _decode(part.get_filename())
                    mime_type = part.get_content_type()
                    if not (filename.lower().endswith(".pdf") or
                            mime_type == "application/pdf"):
                        continue
                    payload = part.get_payload(decode=True) or b""
                    attachment_id = str(index)
                    self._attachment_cache[(uid, attachment_id)] = payload
                    attachments.append(GmailAttachment(
                        message_id=uid,
                        attachment_id=attachment_id,
                        filename=filename or f"adjunto-{uid}-{index}.pdf",
                        mime_type=mime_type,
                        size=len(payload),
                    ))
                messages.append(GmailMessage(
                    id=uid,
                    sender=_decode(parsed.get("From")),
                    subject=_decode(parsed.get("Subject")),
                    date=_decode(parsed.get("Date")),
                    attachments=attachments,
                ))
            return messages
        finally:
            try:
                conn.logout()
            except imaplib.IMAP4.error:
                pass

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        try:
            return self._attachment_cache[(message_id, attachment_id)]
        except KeyError as exc:
            raise KeyError("El adjunto ya no está disponible en esta sesión") from exc


def build_imap_client() -> "ReadOnlyIMAPClient | None":
    config = IMAPConfig.from_env()
    return ReadOnlyIMAPClient(config) if config is not None else None
