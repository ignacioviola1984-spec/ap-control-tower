"""Configuración fail-closed del asistente AP."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in {"1", "true", "yes"}


def agent_api_key() -> str | None:
    """Obtiene la clave inyectada en runtime; nunca existe un fallback en código."""
    value = os.environ.get("OPENAI_API_KEY", "").strip()
    return value or None


#: Esfuerzos aceptados por la Responses API para modelos de razonamiento.
_REASONING_EFFORTS = {"minimal", "low", "medium", "high"}


@dataclass(frozen=True)
class AgentSettings:
    enabled: bool
    model: str
    max_history_messages: int
    max_output_tokens: int
    #: Cadena vacía omite el parámetro (modelos sin razonamiento lo rechazan).
    reasoning_effort: str = "low"

    @classmethod
    def from_env(cls) -> "AgentSettings":
        def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(os.environ.get(name, str(default)))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        def _effort() -> str:
            value = os.environ.get("AP_AGENT_REASONING_EFFORT", "low").strip().casefold()
            if value in {"", "none", "off"}:
                return ""
            return value if value in _REASONING_EFFORTS else "low"

        return cls(
            enabled=_enabled("AP_AGENT_ENABLED"),
            model=os.environ.get("AP_AGENT_MODEL", "gpt-5-mini").strip()
            or "gpt-5-mini",
            max_history_messages=_bounded_int(
                "AP_AGENT_MAX_HISTORY_MESSAGES", 6, 0, 12
            ),
            # El presupuesto se comparte con los tokens de razonamiento: un techo
            # bajo agota la respuesta antes de que el modelo emita el function_call.
            max_output_tokens=_bounded_int(
                "AP_AGENT_MAX_OUTPUT_TOKENS", 2000, 500, 8000
            ),
            reasoning_effort=_effort(),
        )

    def availability_message(self) -> str | None:
        if not self.enabled:
            return "El asistente todavía no está habilitado en este entorno."
        if not agent_api_key():
            return "El asistente está pendiente de configuración por el administrador."
        return None


def admin_dashboard_enabled() -> bool:
    return _enabled("AP_AGENT_ADMIN_ENABLED") and bool(
        os.environ.get("AP_AGENT_ADMIN_PASSWORD", "").strip()
    )
