"""Drop projects.gate_policy with the PR gate.

The gate demanded a suite named ``curated_50_*`` that nothing could create once
the curation product was removed, and it executed the system under test
in-process -- the one thing a tracker does not do. Its only configuration was
this column, whose sole honoured key was ``mode: "warn"``.

``baseline_run_id`` stays: pinning a reference run is what every comparison is
read against, gate or no gate.

Revision ID: 0015_drop_gate_policy
Revises: 0014_run_invalidation
Create Date: 2026-08-03 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_drop_gate_policy"
down_revision = "0014_run_invalidation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove the gate's only configuration."""
    op.drop_column("projects", "gate_policy")


def downgrade() -> None:
    """Restore the column, empty. The policies themselves are not recoverable."""
    op.add_column(
        "projects",
        sa.Column(
            "gate_policy",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
