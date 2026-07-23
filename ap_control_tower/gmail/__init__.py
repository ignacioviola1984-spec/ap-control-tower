"""Integracion de correo READ-ONLY, reutilizable por demo y trial.

Alcance estrictamente de solo lectura (scope gmail.readonly): listar mensajes con
una etiqueta (AP-DEMO), leer remitente/asunto/fecha/adjuntos y descargar los PDF
seleccionados. NUNCA envia, borra, archiva, marca ni modifica etiquetas.

Credenciales por variables de entorno / Secret Manager (nunca en Git ni en la
imagen): client id, client secret y refresh token OAuth. Las librerias de Google
se importan PEREZOSAMENTE: importar este paquete no requiere tenerlas. Los tests
usan FakeGmailClient (sin red).
"""

from __future__ import annotations

from .client import (
    SCOPES,
    GmailAttachment,
    GmailConfig,
    GmailMessage,
    RealGmailClient,
    build_client,
    gmail_configured,
    mailbox_address,
    mailbox_configured,
    mailbox_provider,
)
from .fake import FakeGmailClient
from .imap_client import IMAPConfig, ReadOnlyIMAPClient, imap_configured

__all__ = [
    "SCOPES", "GmailAttachment", "GmailConfig", "GmailMessage",
    "RealGmailClient", "build_client", "gmail_configured", "mailbox_address",
    "mailbox_configured", "mailbox_provider", "IMAPConfig", "ReadOnlyIMAPClient", "imap_configured",
    "FakeGmailClient",
]
