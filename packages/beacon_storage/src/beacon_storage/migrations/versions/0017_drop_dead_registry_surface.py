"""Drop four dead eval_items columns.

All four were write-only or wholly unreferenced: ``embedding`` was a
pass-through parameter nothing supplied, ``evidence`` duplicated what lives in
``item_input``, and ``rejected_at``/``deferred_at`` belonged to the curation
workflow removed with it. (``dataset_loads`` was nearly dropped here too on a
truncated grep; ``beacon benchmarks ingest`` writes it, and the census that
misses a consumer is how a live table gets deleted.)

Revision ID: 0017_drop_dead_registry
Revises: 0016_config_identity
Create Date: 2026-08-03 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_drop_dead_registry"
down_revision = "0016_config_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove the dead columns."""
    op.drop_column("eval_items", "embedding")
    op.drop_column("eval_items", "evidence")
    op.drop_column("eval_items", "rejected_at")
    op.drop_column("eval_items", "deferred_at")


def downgrade() -> None:
    """Restore the shapes, empty. The contents were never written."""
    op.add_column(
        "eval_items", sa.Column("deferred_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column(
        "eval_items", sa.Column("rejected_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column("eval_items", sa.Column("evidence", sa.Text(), nullable=True))
    op.add_column(
        "eval_items", sa.Column("embedding", sa.dialects.postgresql.JSONB(), nullable=True)
    )
