"""solutions, runs, results, verdicts, traces.

Revision ID: 0003_solutions_runs_results
Revises: 0002_rls_policies
Create Date: 2026-06-05 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_solutions_runs_results"
down_revision = "0002_rls_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create solutions, runs, results, verdicts, and traces tables."""
    op.create_table(
        "solutions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("solution_id", sa.String(100), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column(
            "owner_team",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "supported_modes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("layers", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "team_id", "solution_id", "version", name="uq_solution_team_id_version"
        ),
    )
    op.create_index("ix_solution_team", "solutions", ["team_id"])

    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "solution_id",
            sa.Uuid(),
            sa.ForeignKey("solutions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("suite", sa.String(200), nullable=False),
        sa.Column("dataset_version", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("pass_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parent_sweep_id", sa.Uuid(), nullable=True),
        sa.Column("config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "project_id",
            "solution_id",
            "suite",
            "dataset_version",
            "mode",
            "pass_idx",
            "parent_sweep_id",
            name="uq_run_project_pass",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_run_team", "runs", ["team_id"])
    op.create_index("ix_run_project", "runs", ["project_id"])
    op.create_index("ix_run_status", "runs", ["status"])

    op.create_table(
        "results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("item_id", sa.String(200), nullable=False),
        sa.Column("attempt_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output_kind", sa.String(40), nullable=False, server_default="json"),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("runtime_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("run_id", "item_id", "attempt_idx", name="uq_result_run_item_attempt"),
    )
    op.create_index("ix_result_team", "results", ["team_id"])
    op.create_index("ix_result_project", "results", ["project_id"])
    op.create_index("ix_result_run", "results", ["run_id"])

    op.create_table(
        "verdicts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "result_id", sa.Uuid(), sa.ForeignKey("results.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("grader", sa.String(100), nullable=False),
        sa.Column("grader_version", sa.String(100), nullable=False),
        sa.Column("criterion", sa.String(100), nullable=False),
        sa.Column("bool_value", sa.Boolean(), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("raw_output", JSONB(), nullable=True),
        sa.Column("canonical_answer", JSONB(), nullable=True),
        sa.Column("answer_hash", sa.String(64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "value IS NULL OR (value >= 0 AND value <= 1)",
            name="ck_verdict_value_range",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_verdict_confidence_range",
        ),
    )
    op.create_index("ix_verdict_result", "verdicts", ["result_id"])
    op.create_index("ix_verdict_team", "verdicts", ["team_id"])
    op.create_index("ix_verdict_answer_hash", "verdicts", ["answer_hash"])

    op.create_table(
        "traces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "result_id",
            sa.Uuid(),
            sa.ForeignKey("results.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("step_tree", JSONB(), nullable=False),
        sa.Column("object_storage_uri", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_trace_team", "traces", ["team_id"])
    op.create_index("ix_trace_project", "traces", ["project_id"])


def downgrade() -> None:
    """Drop solutions, runs, results, verdicts, and traces tables."""
    for table in ("traces", "verdicts", "results", "runs", "solutions"):
        op.drop_table(table)
