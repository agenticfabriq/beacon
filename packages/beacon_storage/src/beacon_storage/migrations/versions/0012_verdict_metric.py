"""Add verdicts.metric.

A run is graded by more than one reading of correctness -- a strict result
match and a tolerant one -- and both belong in the record. Benchmark adapters
rename graders per suite, so ``verdicts.grader`` is a display label; ``metric``
is the stable key a persisted verdict is aggregated under.

Nullable: verdicts written before this, and graders that contribute no named
metric, carry NULL.

Revision ID: 0012_verdict_metric
Revises: 0011_drop_antigoodhart
Create Date: 2026-08-02 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_verdict_metric"
down_revision = "0011_drop_antigoodhart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable metric column and an index for per-metric aggregation."""
    op.add_column("verdicts", sa.Column("metric", sa.String(length=100), nullable=True))
    op.create_index("ix_verdict_metric", "verdicts", ["metric"])


def downgrade() -> None:
    """Drop the metric column and its index."""
    op.drop_index("ix_verdict_metric", table_name="verdicts")
    op.drop_column("verdicts", "metric")
