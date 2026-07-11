"""FakeGmailClient para pruebas: sin red, misma interfaz que RealGmailClient.

Devuelve mensajes y adjuntos predefinidos. Los tests inyectan los bytes del PDF
para ejercitar el mismo procesamiento que la carga manual. NUNCA toca Gmail real
ni expone operaciones de escritura.
"""

from __future__ import annotations

from .client import GmailAttachment, GmailMessage


def _default_messages() -> list:
    return [
        GmailMessage(
            id="msg-1",
            sender="Proveedor Uno <facturas@proveedor-uno.example>",
            subject="Factura marzo AP-DEMO",
            date="Tue, 07 Jul 2026 09:12:00 +0000",
            attachments=[GmailAttachment(
                message_id="msg-1", attachment_id="att-1",
                filename="factura-uno.pdf", mime_type="application/pdf", size=1024)],
        ),
        GmailMessage(
            id="msg-2",
            sender="Proveedor Dos <cobros@proveedor-dos.example>",
            subject="Proforma AP-DEMO",
            date="Wed, 08 Jul 2026 14:30:00 +0000",
            attachments=[GmailAttachment(
                message_id="msg-2", attachment_id="att-2",
                filename="proforma-dos.pdf", mime_type="application/pdf", size=2048)],
        ),
    ]


class FakeGmailClient:
    """Cliente de mentira (solo lectura) para tests y demos sin credenciales."""

    def __init__(self, messages: list | None = None,
                 attachment_bytes: bytes = b"%PDF-1.4 fake",
                 bytes_by_attachment: dict | None = None) -> None:
        self._messages = messages if messages is not None else _default_messages()
        self._bytes = attachment_bytes
        self._bytes_by_attachment = bytes_by_attachment or {}

    def list_messages(self, max_results: int = 50) -> list:
        return list(self._messages[:max_results])

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        return self._bytes_by_attachment.get(attachment_id, self._bytes)
