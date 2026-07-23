"""Eval del contrato público del producto unificado. Exit 0 = verde.

Verifica que el acceso, la navegación, el wording y ambos entrypoints respondan
como un único producto operativo. No usa red externa ni realiza despliegues.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(label)


def _boot(entry: str, timeout_s: float = 45.0) -> bool:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in (
            "PATH",
            "SYSTEMROOT",
            "SYSTEMDRIVE",
            "TEMP",
            "TMP",
            "COMSPEC",
            "PATHEXT",
            "WINDIR",
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "PROGRAMDATA",
            "LANG",
            "PYTHONPATH",
        )
    }
    env["AP_SYSTEM_PASSWORD"] = "eval-arranque"
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / entry),
            "--server.port",
            str(port),
            "--server.address",
            "127.0.0.1",
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    health_url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.monotonic() + timeout_s
    healthy = False
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with urllib.request.urlopen(health_url, timeout=2) as response:
                    if response.status == 200 and response.read().strip() == b"ok":
                        healthy = True
                        break
            except OSError:
                time.sleep(0.5)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    return healthy


def _public_source() -> str:
    paths = [
        ROOT / "app.py",
        ROOT / "app_trial.py",
        ROOT / "ap_control_tower" / "ui" / "auth.py",
        ROOT / "ap_control_tower" / "ui" / "bootstrap.py",
        ROOT / "ap_control_tower" / "ui" / "pilot_shell.py",
        ROOT / "ap_control_tower" / "ui" / "pilot_pages_common.py",
        ROOT / "ap_control_tower" / "ui" / "pilot_pages_documents.py",
        ROOT / "ap_control_tower" / "ui" / "pilot_pages_workflow.py",
        ROOT / "ap_control_tower" / "ui" / "pilot_pages_reporting.py",
        ROOT / "ap_control_tower" / "ui" / "trial" / "intake.py",
        ROOT / "ap_control_tower" / "ui" / "components" / "gmail_panel.py",
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _check_password_contract() -> None:
    from ap_control_tower.ui import auth

    original_primary = os.environ.get(auth.PRIMARY_PASSWORD_ENV_VAR)
    original_legacy = os.environ.get(auth.LEGACY_PASSWORD_ENV_VAR)
    try:
        os.environ.pop(auth.PRIMARY_PASSWORD_ENV_VAR, None)
        os.environ.pop(auth.LEGACY_PASSWORD_ENV_VAR, None)
        check(auth.configured_password() is None, "sin configuración no hay contraseña")
        os.environ[auth.LEGACY_PASSWORD_ENV_VAR] = "compatibilidad"
        check(
            auth.configured_password() == "compatibilidad",
            "la variable anterior sigue funcionando temporalmente",
        )
        os.environ[auth.PRIMARY_PASSWORD_ENV_VAR] = "producto"
        check(
            auth.configured_password() == "producto",
            "AP_SYSTEM_PASSWORD tiene prioridad",
        )
        check(
            auth.verify_password("producto", "producto")
            and not auth.verify_password("incorrecta", "producto"),
            "la validación de contraseña acepta solo la coincidencia exacta",
        )
    finally:
        if original_primary is None:
            os.environ.pop(auth.PRIMARY_PASSWORD_ENV_VAR, None)
        else:
            os.environ[auth.PRIMARY_PASSWORD_ENV_VAR] = original_primary
        if original_legacy is None:
            os.environ.pop(auth.LEGACY_PASSWORD_ENV_VAR, None)
        else:
            os.environ[auth.LEGACY_PASSWORD_ENV_VAR] = original_legacy


def _check_apptest() -> None:
    from streamlit.testing.v1 import AppTest

    previous_password = os.environ.get("AP_SYSTEM_PASSWORD")
    previous_preview = os.environ.get("AP_PREVIEW_MODE")
    try:
        os.environ["AP_SYSTEM_PASSWORD"] = "revision-local"
        os.environ["AP_PREVIEW_MODE"] = "1"
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        check(not app.exception, "el login abre sin excepciones")
        check(
            [item.value for item in app.title]
            == ["Torre de Control para Cuentas a Pagar"],
            "el título de acceso usa el nombre del producto",
        )
        check(
            [item.value for item in app.subheader] == ["Acceso al Sistema"],
            "el acceso no usa wording de demostración",
        )
        check(
            len(app.text_input) == 1 and app.text_input[0].label == "Contraseña",
            "el campo de acceso tiene label visible y correcto",
        )
        app.text_input(key="_system_password_input").input("revision-local")
        app.button[0].click()
        app.run()
        check(not app.exception, "el producto autenticado abre sin excepciones")
        check(
            any(item.value == "Inicio" for item in app.title),
            "el acceso válido conduce al inicio operativo",
        )
        metric_labels = {item.label for item in app.metric}
        check(
            {"Documentos recibidos", "Pendientes de revisión", "Aprobados para propuesta"}
            <= metric_labels,
            "Inicio muestra indicadores de trabajo accionables",
        )
        operational_pages = [
            ("app_pages/ingreso_documentos.py", "Ingreso de documentos"),
            ("app_pages/documentos.py", "Documentos"),
            ("app_pages/revision_humana.py", "Revisión humana"),
            ("app_pages/propuesta_pago.py", "Lote de pago"),
            ("app_pages/auditoria.py", "Auditoría"),
            ("app_pages/indicadores.py", "Indicadores"),
        ]
        for page_path, title in operational_pages:
            app.switch_page(page_path).run()
            check(
                not app.exception and any(item.value == title for item in app.title),
                f"la página {title} abre con datos sintéticos sin excepciones",
            )
    finally:
        if previous_password is None:
            os.environ.pop("AP_SYSTEM_PASSWORD", None)
        else:
            os.environ["AP_SYSTEM_PASSWORD"] = previous_password
        if previous_preview is None:
            os.environ.pop("AP_PREVIEW_MODE", None)
        else:
            os.environ["AP_PREVIEW_MODE"] = previous_preview


def main() -> int:
    try:
        import streamlit  # noqa: F401
    except Exception:
        print("== Producto unificado: SALTEADO (Streamlit no instalado) ==")
        return 0

    print("== Producto unificado y navegación ==")
    from ap_control_tower.ui import bootstrap, pilot_shell

    expected_pages = [
        "Inicio",
        "Ingreso de documentos",
        "Documentos",
        "Revisión humana",
        "Lote de pago",
        "Auditoría",
        "Indicadores",
    ]
    check(
        [page["title"] for page in pilot_shell.PAGES] == expected_pages,
        "la navegación contiene las siete páginas operativas en el orden esperado",
    )
    check(
        all(bootstrap.normalize_mode(value) == "product" for value in (None, "demo", "trial", "otro")),
        "todos los entrypoints históricos conducen al producto unificado",
    )
    check(
        all(
            "pilot_views" in path.read_text(encoding="utf-8")
            for path in (ROOT / "app_pages").glob("*.py")
        ),
        "todas las páginas usan la capa operativa unificada",
    )

    print("== Wording público y componentes ==")
    source = _public_source()
    for forbidden in (
        "Cargá tus facturas reales y verás cómo el agente las procesa en tiempo real",
        "Prueba de concepto con facturas reales",
        "Extracción, revisión y propuesta de pago en un circuito completo",
        "Acceso a la demo",
        "Password de la demo",
        "Contraseña de la demo",
        "AP Control Tower",
    ):
        check(forbidden.casefold() not in source.casefold(), f"no aparece el texto prohibido: {forbidden}")
    for required in (
        "Torre de Control para Cuentas a Pagar",
        "Brand UP",
        "Acceso al Sistema",
        "Contraseña",
    ):
        check(required in source, f"aparece el wording requerido: {required}")
    check("use_container_width" not in source, "las páginas nuevas usan la API width actual")
    check("st.page_link" not in source, "la navegación interna se mantiene en el sistema principal")

    print("== Acceso y recorrido local ==")
    _check_password_contract()
    _check_apptest()

    if "--sin-app" in sys.argv:
        print("== Arranque HTTP local: SALTEADO (--sin-app) ==")
    else:
        print("== Arranque HTTP local ==")
        check(_boot("app.py"), "app.py responde en el health endpoint local")
        check(
            _boot("app_trial.py"),
            "app_trial.py conserva compatibilidad y abre el mismo producto",
        )

    print()
    if failures:
        print(f"PRODUCTO UNIFICADO ROJO: {len(failures)} fallas")
        return 1
    print("PRODUCTO UNIFICADO VERDE: acceso, wording, navegación y arranque validados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
