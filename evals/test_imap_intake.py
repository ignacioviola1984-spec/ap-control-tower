"""Eval hermético del buzón IMAP en modo estrictamente de solo lectura."""

from __future__ import annotations

import os
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(label)


def _message_bytes() -> bytes:
    msg = EmailMessage()
    msg["From"] = "Proveedor <facturas@proveedor.example>"
    msg["To"] = "ap@example.com"
    msg["Subject"] = "Factura AP-DEMO"
    msg["Date"] = "Sat, 11 Jul 2026 20:00:00 +0000"
    msg.set_content("Adjunto factura")
    msg.add_attachment(b"%PDF-1.4 prueba", maintype="application",
                       subtype="pdf", filename="factura.pdf")
    return msg.as_bytes()


class FakeIMAPConnection:
    instances: list = []

    def __init__(self, host: str, port: int) -> None:
        self.calls = [("connect", host, port)]
        self.__class__.instances.append(self)

    def login(self, user: str, password: str):
        self.calls.append(("login", user, password))
        return "OK", []

    def select(self, folder: str, readonly: bool = False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"1"]

    def uid(self, command: str, *args):
        self.calls.append(("uid", command, *args))
        if command == "search":
            return "OK", [b"42"]
        if command == "fetch":
            return "OK", [(b"42 (BODY[])", _message_bytes()), b")"]
        raise AssertionError(f"comando IMAP inesperado: {command}")

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", []


def main() -> int:
    from ap_control_tower.gmail import IMAPConfig, ReadOnlyIMAPClient, build_client

    print("== Configuración por entorno y prioridad IMAP ==")
    keys = ["AP_IMAP_HOST", "AP_IMAP_PORT", "AP_IMAP_USER",
            "AP_IMAP_PASSWORD", "AP_IMAP_FOLDER"]
    old = {key: os.environ.get(key) for key in keys}
    try:
        os.environ.update({
            "AP_IMAP_HOST": "mail.example.com",
            "AP_IMAP_PORT": "993",
            "AP_IMAP_USER": "ap@example.com",
            "AP_IMAP_PASSWORD": "secret-test-only",
            "AP_IMAP_FOLDER": "AP-DEMO",
        })
        cfg = IMAPConfig.from_env()
        check(cfg is not None and cfg.folder == "AP-DEMO",
              "IMAP se configura sin datos hardcodeados")
        check(isinstance(build_client(), ReadOnlyIMAPClient),
              "build_client prioriza IMAP cuando está configurado")
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("== Protocolo estrictamente de solo lectura ==")
    cfg = IMAPConfig("mail.example.com", 993, "ap@example.com",
                     "secret-test-only", "AP-DEMO")
    client = ReadOnlyIMAPClient(cfg, connection_factory=FakeIMAPConnection)
    messages = client.list_messages()
    check(len(messages) == 1 and messages[0].subject == "Factura AP-DEMO",
          "lista y parsea el mensaje")
    check(len(messages[0].attachments) == 1 and
          messages[0].attachments[0].filename == "factura.pdf",
          "detecta únicamente el PDF adjunto")
    att = messages[0].attachments[0]
    check(client.download_attachment(att.message_id, att.attachment_id).startswith(b"%PDF"),
          "entrega los bytes del PDF")
    calls = FakeIMAPConnection.instances[-1].calls
    check(("select", "AP-DEMO", True) in calls,
          "abre la carpeta con readonly=True")
    commands = [call[1].lower() for call in calls if call[0] == "uid"]
    check(commands == ["search", "fetch"],
          "solo ejecuta SEARCH y FETCH")
    public = {name for name in dir(ReadOnlyIMAPClient) if not name.startswith("_")}
    check(public == {"list_messages", "download_attachment"},
          "no expone enviar, borrar, mover ni marcar")

    if failures:
        print(f"\nIMAP INTAKE ROJO: {len(failures)} fallo(s)")
        return 1
    print("\nIMAP INTAKE VERDE: buzón externo estrictamente de solo lectura")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
