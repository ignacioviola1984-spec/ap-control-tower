"""Regresión: cada cambio de vista fuerza el contenido al inicio."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from ap_control_tower.ui import navigation

    calls: list[tuple[str, int]] = []
    state: dict = {}
    original_state = navigation.st.session_state
    original_html = navigation.components.html
    try:
        navigation.st.session_state = state
        navigation.components.html = (
            lambda body, height=0: calls.append((body, height)))

        navigation.scroll_to_top_on_change("Vista A", state_key="_test_view")
        navigation.scroll_to_top_on_change("Vista A", state_key="_test_view")
        navigation.scroll_to_top_on_change("Vista B", state_key="_test_view")
    finally:
        navigation.st.session_state = original_state
        navigation.components.html = original_html

    assert len(calls) == 2, "solo debe actuar al cambiar de vista"
    assert all(height == 0 for _, height in calls), "el helper no ocupa espacio"
    assert all("scrollTop = 0" in body and "1000" in body for body, _ in calls), (
        "debe subir todos los contenedores y reintentar tras el rerun")
    assert state["_test_view"] == "Vista B"
    print("NAVIGATION SCROLL VERDE: Demo y Trial vuelven al inicio al cambiar de vista")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
