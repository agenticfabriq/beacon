"""Give a run's configuration an identity, and carry it into attribution.

A benchmark row is (model x config), and neither was addressable. ``model_id``
sat inside ``runs.config`` JSONB with no index and no group-by, so nothing could
decide which runs share a row; ``attributions`` carried the solution version and
suite but no configuration at all, so "does the verifier help?" averaged over
whatever models happened to be swept and could not be asked per model.

``config_digest`` is a deterministic hash of the runtime config with secrets
excluded -- it makes grouping correct. It is backfilled here in Python, with the
same helper the application uses, because a SQL reimplementation that drifted
from it would silently split rows. ``config_label`` is what the runner calls
that configuration -- it makes grouping readable. Both nullable: runs written
before this are backfilled where the JSONB carries the value and left NULL where
it does not, rather than being given an invented identity.

Revision ID: 0016_config_identity
Revises: 0015_drop_gate_policy
Create Date: 2026-08-03 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from beacon_storage.config_identity import config_digest

revision = "0016_config_identity"
down_revision = "0015_drop_gate_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the identity columns, backfill from JSONB, and index for grouping."""
    op.add_column("runs", sa.Column("model_id", sa.String(length=200), nullable=True))
    op.add_column("runs", sa.Column("config_label", sa.String(length=200), nullable=True))
    op.add_column("runs", sa.Column("config_digest", sa.String(length=64), nullable=True))
    op.add_column("attributions", sa.Column("model_id", sa.String(length=200), nullable=True))
    op.add_column("attributions", sa.Column("config_digest", sa.String(length=64), nullable=True))

    # Backfill only what the stored config actually says. A run whose config
    # names no model keeps NULL: "unknown" and "unlabelled" are different from
    # any value we could make up here.
    op.execute(
        sa.text(
            "UPDATE runs SET model_id = config ->> 'model_id' "
            "WHERE config ->> 'model_id' IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE runs SET config_label = config -> 'extras' ->> 'config_label' "
            "WHERE config -> 'extras' ->> 'config_label' IS NOT NULL"
        )
    )

    # The digest must match what the application computes, so it is filled in
    # Python with the same helper rather than reimplemented in SQL.
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, config FROM runs")).all()
    for run_id, config in rows:
        connection.execute(
            sa.text("UPDATE runs SET config_digest = :digest WHERE id = :id"),
            {"digest": config_digest(config or {}), "id": run_id},
        )

    op.create_index("ix_runs_model", "runs", ["model_id"])
    op.create_index("ix_runs_config_digest", "runs", ["config_digest"])
    op.create_index(
        "ix_runs_project_model_digest", "runs", ["project_id", "model_id", "config_digest"]
    )


def downgrade() -> None:
    """Drop the identity columns. The digests are recomputable from config."""
    op.drop_index("ix_runs_project_model_digest", table_name="runs")
    op.drop_index("ix_runs_config_digest", table_name="runs")
    op.drop_index("ix_runs_model", table_name="runs")
    op.drop_column("attributions", "config_digest")
    op.drop_column("attributions", "model_id")
    op.drop_column("runs", "config_digest")
    op.drop_column("runs", "config_label")
    op.drop_column("runs", "model_id")
