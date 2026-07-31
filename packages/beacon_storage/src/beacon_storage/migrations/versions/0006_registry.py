"""eval_items, provenance_events, suites, eval_item_suites, production_traces.

Revision ID: 0006_registry
Revises: 0005_attributions
Create Date: 2026-06-05 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0006_registry"
down_revision = "0005_attributions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create eval-item registry, provenance, suites, production_traces, and dataset_loads."""
    op.create_table(
        "eval_items",
        sa.Column("item_id", sa.Uuid(), primary_key=True),
        sa.Column("valid_from", sa.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column("valid_to", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("tier", sa.String(40), nullable=False),
        sa.Column("suite", sa.String(200), nullable=False),
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("solution_id", sa.String(200), nullable=True),
        sa.Column("dataset_version", sa.String(100), nullable=False),
        sa.Column("item_input", JSONB(), nullable=False),
        sa.Column("gold_answer", JSONB(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("question_hash", sa.String(64), nullable=True),
        sa.Column("embedding", ARRAY(sa.Float()), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("rejected_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deferred_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "tier IN ('model_proposed','execution_confirmed','human_verified')",
            name="ck_eval_items_tier",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_eval_items_valid_to_after_from",
        ),
    )
    op.create_index("ix_eval_items_suite_tier", "eval_items", ["suite", "tier"])
    op.create_index("ix_eval_items_team", "eval_items", ["team_id"])
    op.create_index("ix_eval_items_question_hash", "eval_items", ["question_hash"])
    op.create_index(
        "ix_eval_items_review_queue",
        "eval_items",
        ["tier", "deferred_at"],
        postgresql_where=sa.text(
            "valid_to IS NULL AND tier = 'execution_confirmed' AND rejected_at IS NULL"
        ),
    )
    op.create_index(
        "ix_eval_items_active_lookup",
        "eval_items",
        ["item_id"],
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_index(
        "uq_eval_items_active",
        "eval_items",
        ["item_id"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_table(
        "provenance_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prior_tier", sa.String(40), nullable=True),
        sa.Column("new_tier", sa.String(40), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "actor_type IN ('system','llm_judge','human')",
            name="ck_provenance_events_actor_type",
        ),
        sa.CheckConstraint(
            "new_tier IN ('model_proposed','execution_confirmed','human_verified')",
            name="ck_provenance_events_new_tier",
        ),
        sa.CheckConstraint(
            "prior_tier IS NULL OR prior_tier IN "
            "('model_proposed','execution_confirmed','human_verified')",
            name="ck_provenance_events_prior_tier",
        ),
    )
    op.create_index("ix_provenance_events_item", "provenance_events", ["item_id", "created_at"])
    op.create_index("ix_provenance_events_team", "provenance_events", ["team_id"])

    op.create_table(
        "suites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("method", sa.String(60), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "method IN ('manual','separability_gain','handpicked')",
            name="ck_suites_method",
        ),
        sa.UniqueConstraint("project_id", "name", name="uq_suites_project_name"),
    )
    op.create_index("ix_suites_project", "suites", ["project_id"])
    op.create_index("ix_suites_team", "suites", ["team_id"])

    op.create_table(
        "eval_item_suites",
        sa.Column(
            "suite_id",
            sa.Uuid(),
            sa.ForeignKey("suites.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("item_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "added_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_eval_item_suites_item", "eval_item_suites", ["item_id"])

    op.create_table(
        "production_traces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("solution_id", sa.String(200), nullable=False),
        sa.Column(
            "api_key_id",
            sa.Uuid(),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("item_input", JSONB(), nullable=False),
        sa.Column("item_output", JSONB(), nullable=False),
        sa.Column("trace_payload", JSONB(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_eval_candidate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "derived_trace_id",
            sa.Uuid(),
            sa.ForeignKey("traces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_production_traces_team", "production_traces", ["team_id"])
    op.create_index("ix_production_traces_solution", "production_traces", ["solution_id"])
    op.create_index(
        "ix_production_traces_unprocessed",
        "production_traces",
        ["created_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )

    op.create_table(
        "dataset_loads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("benchmark_name", sa.String(80), nullable=False),
        sa.Column("suite", sa.String(200), nullable=False),
        sa.Column("dataset_version", sa.String(100), nullable=False),
        sa.Column("source_path", sa.String(1024), nullable=False),
        sa.Column("target_schema", sa.String(120), nullable=False),
        sa.Column("row_counts", JSONB(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_dataset_loads_benchmark_suite", "dataset_loads", ["benchmark_name", "suite"]
    )
    op.create_index("ix_dataset_loads_sha", "dataset_loads", ["source_sha256"])

    op.execute("ALTER TABLE eval_items ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE eval_items FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY eval_items_visible ON eval_items FOR ALL
        USING (
            current_user_id() IS NULL
            OR eval_items.team_id IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND m.scope_kind = 'team'
                  AND m.scope_id = eval_items.team_id
            )
        );
        """
    )

    op.execute("ALTER TABLE provenance_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE provenance_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY provenance_events_visible ON provenance_events FOR ALL
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND m.scope_kind = 'team'
                  AND m.scope_id = provenance_events.team_id
            )
        );
        """
    )

    op.execute("ALTER TABLE suites ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE suites FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY suites_visible ON suites FOR ALL
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND (
                    (m.scope_kind = 'team' AND m.scope_id = suites.team_id)
                    OR (m.scope_kind = 'project' AND m.scope_id = suites.project_id)
                  )
            )
        );
        """
    )

    op.execute("ALTER TABLE eval_item_suites ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE eval_item_suites FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY eval_item_suites_visible ON eval_item_suites FOR ALL
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM suites s
                JOIN memberships m ON m.user_id = current_user_id()
                WHERE s.id = eval_item_suites.suite_id
                  AND (
                    (m.scope_kind = 'team' AND m.scope_id = s.team_id)
                    OR (m.scope_kind = 'project' AND m.scope_id = s.project_id)
                  )
            )
        );
        """
    )

    op.execute("ALTER TABLE production_traces ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE production_traces FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY production_traces_visible ON production_traces FOR ALL
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND m.scope_kind = 'team'
                  AND m.scope_id = production_traces.team_id
            )
        );
        """
    )


def downgrade() -> None:
    """Drop the registry tables introduced by 0006."""
    op.drop_table("dataset_loads")

    for table in (
        "production_traces",
        "eval_item_suites",
        "suites",
        "provenance_events",
        "eval_items",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_visible ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
        op.drop_table(table)
