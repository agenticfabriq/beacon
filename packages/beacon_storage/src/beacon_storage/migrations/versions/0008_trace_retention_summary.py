"""Add trace retention summary column.

Revision ID: 0008_trace_retention_summary
Revises: 0007_workers_antigoodhart
Create Date: 2026-06-05 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_trace_retention_summary"
down_revision = "0007_workers_antigoodhart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the ``summary`` JSONB column to the traces table."""
    op.add_column("traces", sa.Column("summary", JSONB(), nullable=True))


def downgrade() -> None:
    """Drop the ``summary`` column from the traces table."""
    op.drop_column("traces", "summary")
