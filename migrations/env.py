"""Entorno Alembic para AP Control Tower.

Resuelve la URL de la base SOLO desde el entorno (AP_DATABASE_URL /
DATABASE_URL) o desde ``-x db_url=...``; nunca desde el repo. El
``target_metadata`` es el de los modelos, para autogenerado de migraciones.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Permite importar el paquete de la app cuando se corre alembic desde la raiz.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ap_control_tower.persistence.config import database_url  # noqa: E402
from ap_control_tower.persistence.models_sql import Base       # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    # Prioridad: -x db_url=... > entorno > alembic.ini
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("db_url"):
        return x_args["db_url"]
    env_url = database_url()
    if env_url:
        return env_url
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return ini_url
    raise RuntimeError(
        "No hay URL de base: definir AP_DATABASE_URL / DATABASE_URL o pasar "
        "-x db_url=... (nunca se hardcodea en el repo)")


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,       # batch para que SQLite soporte ALTERs
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
