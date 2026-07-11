"""Eval: contrato de los dos modos (demo y trial). exit 0 = verde.

- La Demo conserva sus vistas y NO muestra "PoC documentos reales" (ahora Gmail).
- La app trial tiene tres vistas internas y un enlace externo separado.
- El enlace a la Demo usa configuracion externa (AP_DEMO_URL).
- Ambos modos ARRANCAN (streamlit sirve el health endpoint).

SKIP con exit 0 si Streamlit no esta instalado (dependencia de UI).
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
    check(any("Gmail AP-DEMO" in k for k in labels),
          "la Demo muestra 'Gmail AP-DEMO'")
    for esperado in ("Corrida del mes", "Cola de excepciones", "Revisión humana",
                     "Aprobación de pagos", "Registro de auditoría", "Caso de negocio"):
        check(any(esperado in k for k in labels), f"la Demo conserva '{esperado}'")

    print("== Trial: tres vistas internas ==")
    from ap_control_tower.ui.trial import shell
    check(len(shell.TRIAL_OPTIONS) == 3, f"trial tiene 3 opciones ({len(shell.TRIAL_OPTIONS)})")
    joined = " | ".join(shell.TRIAL_OPTIONS)
    check("Probar con mis facturas" in joined, "opción 'Probar con mis facturas'")
    check("Ver resultados con mis facturas" in joined, "opción 'Ver resultados con mis facturas'")
    check("Consultar caso de negocio" in joined, "opción 'Consultar caso de negocio'")
    check("Abrir" not in joined and "Demo completa" not in joined,
          "el enlace a la Demo NO es una vista del selector")

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
