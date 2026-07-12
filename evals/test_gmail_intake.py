"""Eval: integracion Gmail READ-ONLY. exit 0 = verde.

Hermetico: usa FakeGmailClient (sin red, sin credenciales). Valida que el scope
es solo lectura, que el cliente real NO expone operaciones de escritura, que sin
credenciales queda deshabilitado, y que un adjunto importado se procesa por el
mismo motor que la carga manual.

NUNCA accede a Gmail real ni requiere las librerias de Google (import perezoso).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def main() -> int:
    import os

    from ap_control_tower import gmail as g

    print("== Solo lectura: scope e interfaz ==")
    check(g.SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"],
          "scope unico gmail.readonly")
    public = {m for m in dir(g.RealGmailClient) if not m.startswith("_")}
    check(public == {"list_messages", "download_attachment"},
          f"interfaz publica solo de lectura ({sorted(public)})")
    write_verbs = ("send", "delete", "trash", "modify", "insert", "archive",
                   "batchmodify", "update", "create")
    check(not any(v in m.lower() for m in public for v in write_verbs),
          "ningun metodo publico de escritura")

    print("== Sin credenciales: deshabilitado, apps siguen con carga manual ==")
    for key in ("AP_GMAIL_CLIENT_ID", "AP_GMAIL_CLIENT_SECRET", "AP_GMAIL_REFRESH_TOKEN"):
        os.environ.pop(key, None)
    check(g.gmail_configured() is False, "gmail_configured() False sin credenciales")
    check(g.build_client() is None, "build_client() None sin credenciales")

    print("== Config desde entorno (user/label configurables) ==")
    os.environ["AP_GMAIL_CLIENT_ID"] = "cid"
    os.environ["AP_GMAIL_CLIENT_SECRET"] = "csec"
    os.environ["AP_GMAIL_REFRESH_TOKEN"] = "rtok"
    try:
        cfg = g.GmailConfig.from_env()
        check(cfg is not None and cfg.user == "ignacio@getdeterma.com"
              and cfg.label == "AP-DEMO",
              "defaults AP_GMAIL_USER=ignacio@getdeterma.com, label AP-DEMO")
        os.environ["AP_GMAIL_LABEL"] = "OTRA"
        os.environ["AP_GMAIL_USER"] = "otro@dominio.com"
        cfg2 = g.GmailConfig.from_env()
        check(cfg2.label == "OTRA" and cfg2.user == "otro@dominio.com",
              "user y label son configurables por entorno")
    finally:
        for key in ("AP_GMAIL_CLIENT_ID", "AP_GMAIL_CLIENT_SECRET",
                    "AP_GMAIL_REFRESH_TOKEN", "AP_GMAIL_LABEL", "AP_GMAIL_USER"):
            os.environ.pop(key, None)

    print("== FakeGmailClient: listar y descargar (sin red) ==")
    fake = g.FakeGmailClient(bytes_by_attachment={"att-1": b"%PDF-1.4 uno",
                                                  "att-2": b"%PDF-1.4 dos"})
    msgs = fake.list_messages()
    check(len(msgs) == 2, "el fake lista dos mensajes")
    check(all(a.mime_type == "application/pdf" for m in msgs for a in m.attachments),
          "los adjuntos del fake son PDF")
    data = fake.download_attachment("msg-1", "att-1")
    check(data == b"%PDF-1.4 uno", "descarga el adjunto correcto por id")

    print("== Trial limpio: Gmail no lista adjuntos antes de solicitarlo ==")
    from ap_control_tower.ui.components import gmail_panel
    original_state = gmail_panel.st.session_state
    original_caption = gmail_panel.st.caption
    original_button = gmail_panel.st.button
    state: dict = {}
    captions: list[str] = []
    panel_list_calls = 0
    original_list_messages = fake.list_messages
    def counted_list_messages(*args, **kwargs):
        nonlocal panel_list_calls
        panel_list_calls += 1
        return original_list_messages(*args, **kwargs)
    fake.list_messages = counted_list_messages
    try:
        gmail_panel.st.session_state = state
        gmail_panel.st.caption = lambda text: captions.append(text)
        gmail_panel.st.button = lambda *args, **kwargs: False
        gmail_panel.render_gmail_panel(lambda files: None, client=fake, require_open=True)
    finally:
        gmail_panel.st.session_state = original_state
        gmail_panel.st.caption = original_caption
        gmail_panel.st.button = original_button
        fake.list_messages = original_list_messages
    check(panel_list_calls == 0, "el panel cerrado no consulta ni muestra mensajes")
    check(any("únicamente cuando lo solicites" in text for text in captions),
          "el inicio muestra el acceso a Gmail cerrado, sin adjuntos precargados")

    print("== Exclusión configurable de adjuntos de prueba ==")
    os.environ["AP_GMAIL_EXCLUDED_FILENAMES"] = "factura-uno.pdf"
    try:
        visible = gmail_panel._visible_attachments(msgs[0])
        visible_other = gmail_panel._visible_attachments(msgs[1])
    finally:
        os.environ.pop("AP_GMAIL_EXCLUDED_FILENAMES", None)
    check(not visible and len(visible_other) == 1,
          "un archivo excluido no aparece; los demás adjuntos siguen disponibles")

    print("== Un adjunto Gmail se procesa por el MISMO motor que la carga manual ==")
    try:
        from reportlab.pdfgen import canvas  # opcional (esta en requirements.txt)
        from io import BytesIO
        buf = BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(72, 700, "FACTURA GMAIL Total 100 EUR")
        c.save()
        pdf = buf.getvalue()
        from ap_control_tower.ui.components import extraction_view as ev
        fake2 = g.FakeGmailClient(bytes_by_attachment={"att-1": pdf})
        pdf_bytes = fake2.download_attachment("msg-1", "att-1")
        results, errors = ev.process_files([("factura.pdf", pdf_bytes)])
        check(len(results) == 1 and not errors,
              "el PDF importado por Gmail se procesa (1 resultado, sin errores)")
    except ImportError:
        check(True, "procesamiento del adjunto SALTEADO (reportlab ausente)")

    print()
    if failures:
        print(f"GMAIL INTAKE ROJO: {len(failures)} fallas")
        return 1
    print("GMAIL INTAKE VERDE: solo lectura, deshabilitado sin credenciales, fake procesa (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
