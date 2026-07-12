"""Eval: contrato de los dos modos (demo y trial). exit 0 = verde.

- La Demo conserva sus vistas y NO muestra "PoC documentos reales" (ahora Gmail).
- La app trial tiene tres vistas internas y un enlace externo separado.
- El enlace a la Demo usa configuracion externa (AP_DEMO_URL).
- Ambos modos ARRANCAN (streamlit sirve el health endpoint).

SKIP con exit 0 si Streamlit no esta instalado (dependencia de UI).
"""

from __future__ import annotations

import inspect
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


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def _boot(entry: str, extra_env: dict, timeout_s: float = 45.0) -> bool:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "COMSPEC",
                    "PATHEXT", "WINDIR", "HOME", "USERPROFILE", "APPDATA",
                    "LOCALAPPDATA", "PROGRAMDATA", "LANG")}
    env["AP_DEMO_PASSWORD"] = "eval-arranque"
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra_env)
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / entry),
         "--server.port", str(port), "--server.address", "127.0.0.1",
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.monotonic() + timeout_s
    ok = False
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status == 200 and resp.read().strip() == b"ok":
                        ok = True
                        break
            except OSError:
                time.sleep(0.5)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return ok


def main() -> int:
    try:
        import streamlit  # noqa: F401
    except Exception:
        print("== App modes: SALTEADO (Streamlit no instalado) ==")
        print("  SKIP  pip install -r requirements.txt")
        return 0

    print("== Demo: vistas y ausencia del PoC ==")
    from ap_control_tower.ui import demo_shell
    labels = list(demo_shell.VIEWS)
    check(not any("PoC" in k for k in labels),
          "la Demo ya NO muestra 'PoC documentos reales'")
    check(not any("Correo AP-DEMO" in k for k in labels),
          "la Demo NO muestra 'Correo AP-DEMO'; Gmail vive solo en Trial")
    for esperado in ("Corrida del mes", "Cola de excepciones", "Revisión humana",
                     "Aprobación de pagos", "Registro de auditoría", "Caso de negocio"):
        check(any(esperado in k for k in labels), f"la Demo conserva '{esperado}'")

    print("== Trial: cinco vistas internas ==")
    from ap_control_tower.ui.trial import shell
    check(len(shell.TRIAL_OPTIONS) == 5, f"trial tiene 5 opciones ({len(shell.TRIAL_OPTIONS)})")
    joined = " | ".join(shell.TRIAL_OPTIONS)
    check("Probar con mis facturas" in joined, "opción 'Probar con mis facturas'")
    check("Ver resultados con mis facturas" in joined, "opción 'Ver resultados con mis facturas'")
    check("Consultar caso de negocio" in joined, "opción 'Consultar caso de negocio'")
    check("Revisión humana" in joined, "opción 'Revisión humana'")
    check("Aprobación para propuesta de pago" in joined,
          "opción 'Aprobación para propuesta de pago'")
    trial_results_source = (
        ROOT / "ap_control_tower" / "ui" / "trial" / "results.py"
    ).read_text(encoding="utf-8")
    check("Corridas anteriores" not in trial_results_source,
          "Resultados muestra solo la sesión actual, sin corridas anteriores")
    shell_source = inspect.getsource(shell.render)
    check("demo_link" not in shell_source and "render_sidebar_end_session" in shell_source,
          "Trial reemplaza el enlace a la Demo por Finalizar sesión")

    print("== Copy comercial y limpieza de metadatos técnicos ==")
    from ap_control_tower.ui import theme
    from ap_control_tower.ui.trial import intake
    from ap_control_tower.ui.trial import payment_approval
    intake_source = inspect.getsource(intake)
    footer_source = inspect.getsource(theme.sidebar_footer)
    check("Cargá tus facturas reales y verás cómo el agente las procesa en tiempo real"
          in intake_source, "Trial usa el mensaje comercial acordado")
    check("apct-trial-hero" in intake_source and "container(border=True)" in intake_source,
          "Trial usa título compacto y cards para las dos vías de carga")
    check("Importar desde el correo AP (" not in intake_source,
          "el título de Gmail no repite carpeta/modo")
    from ap_control_tower.ui.components import gmail_panel
    gmail_source = inspect.getsource(gmail_panel)
    check("Adjuntos PDF a importar (carpeta AP-DEMO)" not in gmail_source
          and "**Adjuntos PDF a importar**" in gmail_source,
          "selector Gmail usa wording limpio y en negrita")
    check("Consultar carpeta AP-DEMO" not in gmail_source
          and "Consultar correo AP" in gmail_source,
          "correo AP no expone el nombre técnico de la etiqueta")
    from ap_control_tower.ui.trial import human_review
    review_source = inspect.getsource(human_review)
    check("Aprobación - propuesta de pago" in review_source,
          "Revisión humana ofrece navegación al siguiente paso")
    brand_source = inspect.getsource(theme.sidebar_brand)
    check("Prueba de concepto con facturas reales" in brand_source,
          "lateral identifica la experiencia como prueba de concepto real")
    check("corrida <code>" not in footer_source and "commit <code>" not in footer_source,
          "la Demo no expone run/commit en el pie")
    check("PoC documental" not in footer_source,
          "la Demo no muestra el wording técnico de Document AI")
    payment_source = inspect.getsource(payment_approval)
    check("Seleccionar todas las elegibles" in payment_source,
          "Aprobación ofrece selección masiva explícita")
    check("Documentos retenidos / fuera de la propuesta" in payment_source,
          "retenidos tienen una sección visible propia")
    check("Confirmar exclusión de la propuesta" not in payment_source
          and "Rechazar para propuesta" not in payment_source,
          "Aprobación elimina acciones duplicadas sobre retenidos")
    check("Ir a Revisión humana" in payment_source,
          "retenidos se gestionan desde Revisión humana")
    check("Exportar lote aprobado CSV" in payment_source
          and "Exportar lote aprobado Excel" in payment_source,
          "lote aprobado se exporta en CSV y Excel")
    check("Autorizar excepción para propuesta de pago" in review_source,
          "Revisión humana puede autorizar una excepción de pago")
    from ap_control_tower.ui.trial.step_navigation import (
        NAVIGATION_KEY, PENDING_KEY, resolve_pending)
    pending_state = {PENDING_KEY: shell.PAYMENT_APPROVAL,
                     NAVIGATION_KEY: shell.HUMAN_REVIEW}
    check(resolve_pending(pending_state, shell.TRIAL_OPTIONS) == shell.PAYMENT_APPROVAL
          and pending_state[NAVIGATION_KEY] == shell.PAYMENT_APPROVAL,
          "navegación aplica el destino antes de instanciar el selector")
    from ap_control_tower.ui.trial import results
    next_sources = "\n".join((inspect.getsource(intake), inspect.getsource(results),
                               inspect.getsource(human_review),
                               inspect.getsource(payment_approval)))
    for next_label in ("Ver resultados con mis facturas", "Revisión humana",
                       "Aprobación - propuesta de pago", "Consultar caso de negocio"):
        check(next_label in next_sources, f"recorrido incluye botón '{next_label}'")
    from ap_control_tower.ui.trial import business_case
    business_source = inspect.getsource(business_case)
    check("apct-method-note" in business_source,
          "Caso de Negocio destaca las notas metodológicas")
    check("business_case_evidence_metrics" in business_source
          and "business_case_asis_metrics" in business_source,
          "Caso de Negocio permite reequilibrar labels y valores")

    print("== Enlace a la Demo por configuración externa ==")
    from ap_control_tower.ui.trial import demo_link
    prev = os.environ.get("AP_DEMO_URL")
    try:
        os.environ.pop("AP_DEMO_URL", None)
        check(demo_link.demo_url() is None, "sin AP_DEMO_URL -> None (no hardcodeada)")
        os.environ["AP_DEMO_URL"] = "https://demo.example/app"
        check(demo_link.demo_url() == "https://demo.example/app",
              "con AP_DEMO_URL -> se usa esa URL")
    finally:
        if prev is None:
            os.environ.pop("AP_DEMO_URL", None)
        else:
            os.environ["AP_DEMO_URL"] = prev

    print("== bootstrap.normalize_mode ==")
    from ap_control_tower.ui import bootstrap
    check(bootstrap.normalize_mode("trial") == "trial", "trial -> trial")
    check(bootstrap.normalize_mode(None) == "demo", "None -> demo")
    check(bootstrap.normalize_mode("otro") == "demo", "valor invalido -> demo")

    if "--sin-app" in sys.argv:
        print("== Arranque de ambos modos: SALTEADO (--sin-app) ==")
    else:
        print("== Ambos modos arrancan (health endpoint) ==")
        check(_boot("app.py", {}), "modo demo arranca (app.py)")
        check(_boot("app_trial.py", {}), "modo trial arranca (app_trial.py)")

    print()
    if failures:
        print(f"APP MODES ROJO: {len(failures)} fallas")
        return 1
    print("APP MODES VERDE: demo sin PoC, trial 3 vistas, enlace externo, ambos arrancan (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
