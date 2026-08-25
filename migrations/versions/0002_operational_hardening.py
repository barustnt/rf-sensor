"""operational dashboard and reliability hardening

Revision ID: 0002_operational_hardening
Revises: 0001_initial
Create Date: 2026-08-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0002_operational_hardening"
down_revision = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("update analysis_jobs set model_version = 'mock-v1' where model_version = 'unknown'")
    op.execute("update model_runs set model_version = 'mock-v1' where model_version = 'unknown'")
    op.create_table(
        "storage_snapshots",
        sa.Column("snapshot_id", sa.String(length=36), primary_key=True),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=True),
        sa.Column("free_bytes", sa.BigInteger(), nullable=True),
        sa.Column("used_percent", sa.Float(), nullable=True),
        sa.Column("artifact_bytes", sa.BigInteger(), nullable=True),
        sa.Column("spool_bytes", sa.BigInteger(), nullable=True),
        sa.Column("pending_items", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_storage_snapshots_target_time",
        "storage_snapshots",
        ["target_type", "target_id", "timestamp_utc"],
    )
    op.create_table(
        "retention_reports",
        sa.Column("report_id", sa.String(length=36), primary_key=True),
        sa.Column("report_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False, server_default="operator"),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("retention_reports")
    op.drop_index("ix_storage_snapshots_target_time", table_name="storage_snapshots")
    op.drop_table("storage_snapshots")
