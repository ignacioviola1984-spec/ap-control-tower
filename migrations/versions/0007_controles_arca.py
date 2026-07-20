"""Controles ARCA: cache de padron y base APOC local.

Revision ID: 0007_controles_arca
Revises: 0006_analytics_views
Create Date: 2026-07-20

Tres tablas nuevas, solo aditivas (nada del comportamiento existente cambia):

* ``arca_padron_cache``: constancia de inscripcion normalizada por CUIT, con
  ``fetched_at`` para el TTL de lectura (default 7 dias).
* ``arca_apoc_versions``: cada descarga de la base APOC con fecha, checksum y
  cantidad de registros (versionado auditable de la base usada).
* ``arca_apoc_entries``: CUITs de la base APOC vigente; el lookup del control
  C11_APOC es siempre local (cero llamadas de red por documento).

Sin datos de produccion involucrados: el downgrade elimina las tablas.
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_controles_arca"
down_revision = "0006_analytics_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "arca_padron_cache",
        sa.Column("cuit", sa.String(length=11), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("estado", sa.String(length=48), nullable=True),
        sa.Column("condicion_iva", sa.String(length=48), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("cuit"),
    )
    op.create_table(
        "arca_apoc_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fecha_descarga", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("cantidad_registros", sa.Integer(), nullable=False),
        sa.Column("origen", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_arca_apoc_versions_checksum"),
                    "arca_apoc_versions", ["checksum"])
    op.create_table(
        "arca_apoc_entries",
        sa.Column("cuit", sa.String(length=11), nullable=False),
        sa.Column("fuente", sa.String(length=255), nullable=True),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["arca_apoc_versions.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("cuit"),
    )
    op.create_index(op.f("ix_arca_apoc_entries_version_id"),
                    "arca_apoc_entries", ["version_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_arca_apoc_entries_version_id"),
                  table_name="arca_apoc_entries")
    op.drop_table("arca_apoc_entries")
    op.drop_index(op.f("ix_arca_apoc_versions_checksum"),
                  table_name="arca_apoc_versions")
    op.drop_table("arca_apoc_versions")
    op.drop_table("arca_padron_cache")
