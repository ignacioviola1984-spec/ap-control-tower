"""esquema inicial de persistencia (Fase 1)

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-11

Crea el modelo relacional completo desde los modelos SQLAlchemy (paridad
garantizada con el codigo). NO destructivo: usa checkfirst, por lo que aplicar
sobre una base ya poblada no borra ni recrea tablas existentes. Las migraciones
siguientes usaran autogenerado / op.* estandar sobre esta linea base.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from ap_control_tower.persistence.models_sql import Base

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # checkfirst=True -> no toca tablas que ya existan (base existente segura).
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
