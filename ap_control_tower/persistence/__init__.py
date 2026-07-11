"""Capa de persistencia OPCIONAL de AP Control Tower (Fase 1).

Aditiva por diseno: sin la variable de entorno ``AP_DATABASE_URL`` (o
``DATABASE_URL``) el sistema se comporta EXACTAMENTE como la demo actual
(estado en session_state + dataset sintetico). El motor (``engine/``) no
importa este paquete: sigue siendo puro y solo-stdlib.

SQLAlchemy y Alembic viven UNICAMENTE aca (ver requirements-persistence.txt).
Importar este subpaquete no arrastra SQLAlchemy salvo que se toquen los
modulos que lo usan; ``is_persistence_available()`` permite degradar sin
romper cuando la dependencia no esta instalada.
"""

from __future__ import annotations

from .config import (
    DatabaseConfig,
    database_url,
    is_persistence_available,
    is_persistence_configured,
)

__all__ = [
    "DatabaseConfig",
    "database_url",
    "is_persistence_available",
    "is_persistence_configured",
]
