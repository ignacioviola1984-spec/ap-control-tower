"""Persist decisions from human review and payment proposal.

Revision ID: 0005_trial_workflow_decisions
Revises: 0004_audit_long_invoice_ids
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_trial_workflow_decisions"
down_revision = "0004_audit_long_invoice_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trial_runs") as batch:
        batch.add_column(sa.Column("review_decisions", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("approval_decisions", sa.JSON(), nullable=True))
    op.execute("UPDATE trial_runs SET review_decisions = '{}' WHERE review_decisions IS NULL")
    op.execute("UPDATE trial_runs SET approval_decisions = '{}' WHERE approval_decisions IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("trial_runs") as batch:
        batch.drop_column("approval_decisions")
        batch.drop_column("review_decisions")
