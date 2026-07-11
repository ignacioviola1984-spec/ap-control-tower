"""Fabrica de engine y sesiones SQLAlchemy para la capa persistente.

El engine se crea a demanda desde la configuracion de entorno. Nada se conecta
al importar: si no hay ``AP_DATABASE_URL`` no se toca la base. Pensado para
inyectar en repositorios y para que Alembic reutilice la misma URL.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import DatabaseConfig
from .models_sql import Base


def _enable_sqlite_fk(engine: Engine) -> None:
    """SQLite no fuerza claves foraneas por defecto; en Postgres ya se fuerzan.
    Lo activamos para que integridad y cascadas se comporten igual en dev."""

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def build_engine(config: DatabaseConfig | None = None) -> Engine:
    """Crea un Engine desde la config (o desde el entorno si no se pasa)."""
    cfg = config or DatabaseConfig.from_env()
    if cfg is None:
        raise RuntimeError(
            "Persistencia no configurada: falta AP_DATABASE_URL / DATABASE_URL")
    connect_args = {}
    if cfg.url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(cfg.url, echo=cfg.echo, future=True,
                          connect_args=connect_args)
    if cfg.url.startswith("sqlite"):
        _enable_sqlite_fk(engine)
    return engine


def build_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def create_all(engine: Engine) -> None:
    """Crea el esquema desde los modelos (dev/tests). En produccion se usan
    las migraciones Alembic, NO esto."""
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Transaccion administrada: commit al salir bien, rollback ante error."""
    factory = build_sessionmaker(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
