"""Add runs.sweep_arm and widen run identity to include the ablation arm.

A NIGHTLY_LOO sweep runs one arm per ablated layer plus a baseline, all under a
single ``parent_sweep_id`` and all at the same ``pass_idx``. The previous
``uq_run_project_pass`` tuple carried no arm discriminator, so the second arm of
every sweep collided with the first. The arm is not derivable from the row --
recovering it from ``config.layers_enabled`` requires already knowing which run
is the baseline -- so it is stored explicitly.

Existing rows get ``sweep_arm = NULL``. Because the constraint keeps
``NULLS NOT DISTINCT``, run identity for every non-sweep run is unchanged.

Revision ID: 0010_run_sweep_arm
Revises: 0009_project_solutions
Create Date: 2026-08-01 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_run_sweep_arm"
down_revision = "0009_project_solutions"
branch_labels = None
depends_on = None

_COLUMNS = (
    "project_id",
    "solution_id",
    "suite",
    "dataset_version",
    "mode",
    "pass_idx",
    "parent_sweep_id",
)


def upgrade() -> None:
    """Add the sweep_arm column and rebuild the run uniqueness constraint."""
    op.add_column("runs", sa.Column("sweep_arm", sa.String(length=100), nullable=True))
    op.drop_constraint("uq_run_project_pass", "runs", type_="unique")
    op.create_unique_constraint(
        "uq_run_project_pass",
        "runs",
        [*_COLUMNS, "sweep_arm"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """Restore the arm-less uniqueness constraint and drop the column."""
    op.drop_constraint("uq_run_project_pass", "runs", type_="unique")
    op.create_unique_constraint(
        "uq_run_project_pass",
        "runs",
        list(_COLUMNS),
        postgresql_nulls_not_distinct=True,
    )
    op.drop_column("runs", "sweep_arm")
