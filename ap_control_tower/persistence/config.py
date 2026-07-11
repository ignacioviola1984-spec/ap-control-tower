"""Configuracion de la base: SOLO desde entorno, nunca credenciales en repo.

La URL de conexion se lee de ``AP_DATABASE_URL`` (preferida) o ``DATABASE_URL``.
Formato SQLAlchemy, p. ej.:
    postgresql+psycopg://usuario:password@host:5432/ap_control_tower
    sqlite+pysqlite:///./ap_local.db      (dev/tests portables)

Nunca se escribe la URL en logs ni se persiste. ``masked_url()`` la ofusca
para diagnostico (oculta el password).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

DB_URL_ENV_VARS = ("AP_DATABASE_URL", "DATABASE_URL")

_PASSWORD_RE = re.compile(r"(://[^:/@]+:)([^@/]+)(@)")


def database_url() -> str | None:
    """URL de conexion desde entorno; None si no hay persistencia configurada."""
    for var in DB_URL_ENV_VARS:
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip()
    return None


def is_persistence_configured() -> bool:
    """True si hay una URL de base en el entorno."""
    return database_url() is not None


def is_persistence_available() -> bool:
    """True si ademas SQLAlchemy esta instalado (dependencia opcional)."""
    if not is_persistence_configured():
        return False
    try:
        import sqlalchemy  # noqa: F401
    except Exception:
        return False
    return True


def masked_url(url: str | None = None) -> str:
    """URL con el password ofuscado, apta para logs/diagnostico."""
    resolved = url if url is not None else database_url()
    if not resolved:
        return "(sin base configurada)"
    return _PASSWORD_RE.sub(r"\1***\3", resolved)


@dataclass(frozen=True)
class DatabaseConfig:
    """Configuracion resuelta de la base persistente."""
    url: str
    echo: bool = False

    @classmethod
    def from_env(cls) -> "DatabaseConfig | None":
        url = database_url()
        if not url:
            return None
        echo = os.environ.get("AP_DB_ECHO", "").lower() in ("1", "true", "yes")
        return cls(url=url, echo=echo)

    @property
    def masked_url(self) -> str:
        return masked_url(self.url)
