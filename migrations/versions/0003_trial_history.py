"""persist trial extraction history

Revision ID: 0003_trial_history
Revises: 0002_lifecycle
Create Date: 2026-07-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_trial_history"
down_revision: Union[str, None] = "0002_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trial_runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("processing_seconds", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "trial_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("doc_id", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("engine", sa.String(length=64), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("text_chars", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("field_confidences", sa.JSON(), nullable=False),
        sa.Column("processing_seconds", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["trial_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "doc_id", name="uq_trial_run_document"),
    )
    op.create_index(op.f("ix_trial_documents_run_id"), "trial_documents", ["run_id"])
    op.create_index(op.f("ix_trial_documents_file_hash"), "trial_documents", ["file_hash"])


def downgrade() -> None:
    op.drop_index(op.f("ix_trial_documents_file_hash"), table_name="trial_documents")
    op.drop_index(op.f("ix_trial_documents_run_id"), table_name="trial_documents")
    op.drop_table("trial_documents")
    op.drop_table("trial_runs")
