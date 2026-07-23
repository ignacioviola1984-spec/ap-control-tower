"""Regresión del alcance visual del asistente. Exit 0 = verde."""

from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _subheaders(app) -> list[str]:
    return [item.value for item in app.subheader]


def main() -> int:
    from streamlit.testing.v1 import AppTest

    previous = {
        name: os.environ.get(name)
        for name in ("AP_SYSTEM_PASSWORD", "AP_PREVIEW_MODE", "AP_AGENT_ENABLED",
                     "OPENAI_API_KEY", "AP_AGENT_ADMIN_ENABLED",
                     "AP_AGENT_ADMIN_PASSWORD")
    }
    try:
        os.environ["AP_SYSTEM_PASSWORD"] = "agent-ui-test"
        os.environ["AP_PREVIEW_MODE"] = "1"
        os.environ["AP_AGENT_ENABLED"] = "1"
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("AP_AGENT_ADMIN_ENABLED", None)
        os.environ.pop("AP_AGENT_ADMIN_PASSWORD", None)

        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        app.text_input(key="_system_password_input").input("agent-ui-test")
        app.button[0].click()
        app.run()
        assert not app.exception, app.exception

        app.switch_page("app_pages/documentos.py").run()
        assert not app.exception, app.exception
        assert "Asistente para este documento" in _subheaders(app)
        assert any(
            "pendiente de configuración" in item.value
            for item in app.info
        )

        app.switch_page("app_pages/revision_humana.py").run()
        assert not app.exception, app.exception
        assert "Asistente para este documento" in _subheaders(app)

        app.switch_page("app_pages/auditoria.py").run()
        assert not app.exception, app.exception
        assert "Asistente para este documento" not in _subheaders(app)

        from ap_control_tower.agent.config import admin_dashboard_enabled

        assert not admin_dashboard_enabled()
        print(
            "AGENT UI VERDE: panel solo en Documentos y Revisión humana; "
            "Auditoría y navegación normal sin superficie administrativa"
        )
        return 0
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(main())
