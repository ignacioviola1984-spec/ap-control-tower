"""Amplia identificadores de auditoria para nombres reales de documentos.

Revision ID: 0004_audit_long_invoice_ids
Revises: 0003_trial_history
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_audit_long_invoice_ids"
down_revision = "0003_trial_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("auditoria") as batch:
        batch.alter_column("entidad_id", existing_type=sa.String(64),
                           type_=sa.String(255), existing_nullable=True)
        batch.alter_column("invoice_id", existing_type=sa.String(48),
                           type_=sa.String(255), existing_nullable=True)
        batch.alter_column("correlation_id", existing_type=sa.String(64),
                           type_=sa.String(255), existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("auditoria") as batch:
        batch.alter_column("correlation_id", existing_type=sa.String(255),
                           type_=sa.String(64), existing_nullable=True)
        batch.alter_column("invoice_id", existing_type=sa.String(255),
                           type_=sa.String(48), existing_nullable=True)
        batch.alter_column("entidad_id", existing_type=sa.String(255),
                           type_=sa.String(64), existing_nullable=True)
