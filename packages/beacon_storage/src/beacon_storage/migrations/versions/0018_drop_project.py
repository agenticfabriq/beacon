"""Remove the Project level: runs hang off the benchmark, teams own suites.

Project sat between team and benchmark and carried nothing of its own — no
version, no config, no meaning a score depends on. The greenfield decision was
to delete it rather than hide it; this is the deletion.

What moves where:
- ``suites`` become team-owned (unique on team+name) and gain
  ``baseline_run_id`` — the reference run is a property of the benchmark.
- ``runs`` gain a real ``suite_id`` FK (backfilled from the config stash
  ``_beacon_suite_id`` where present, else by team+suite-name match) and lose
  ``project_id``; run identity is now suite-anchored.
- ``results``/``verdicts``/``traces``/``attributions`` lose ``project_id``.
- RLS on those tables becomes team-only.
- ``project_solutions`` and ``projects`` are dropped; project-scoped
  memberships are deleted, not migrated — team membership already covers
  everything they granted.

Revision ID: 0018_drop_project
Revises: 0017_drop_dead_registry
Create Date: 2026-08-03 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_drop_project"
down_revision = "0017_drop_dead_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Re-anchor runs on suites, then drop everything project-shaped."""
    connection = op.get_bind()

    # Suites first: team ownership and the reference run.
    op.add_column("suites", sa.Column("baseline_run_id", sa.Uuid(), nullable=True))
    op.drop_constraint("uq_suites_project_name", "suites", type_="unique")
    op.create_unique_constraint("uq_suites_team_name", "suites", ["team_id", "name"])
    connection.execute(
        sa.text(
            "UPDATE suites s SET baseline_run_id = p.baseline_run_id "
            "FROM projects p WHERE p.id = s.project_id "
            "AND p.baseline_run_id IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM runs r WHERE r.id = p.baseline_run_id "
            "            AND r.suite = s.name)"
        )
    )

    # Runs: a real suite FK, backfilled from the config stash else by name.
    op.add_column("runs", sa.Column("suite_id", sa.Uuid(), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE runs r SET suite_id = (config ->> '_beacon_suite_id')::uuid "
            "WHERE config ->> '_beacon_suite_id' IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM suites s "
            "            WHERE s.id = (r.config ->> '_beacon_suite_id')::uuid)"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE runs r SET suite_id = s.id FROM suites s "
            "WHERE r.suite_id IS NULL AND s.team_id = r.team_id AND s.name = r.suite"
        )
    )
    orphans = connection.execute(
        sa.text("SELECT count(*) FROM runs WHERE suite_id IS NULL")
    ).scalar_one()
    if orphans:
        raise RuntimeError(
            f"{orphans} runs name a suite that does not exist; create it before migrating"
        )
    op.alter_column("runs", "suite_id", nullable=False)
    op.create_foreign_key(
        "fk_runs_suite", "runs", "suites", ["suite_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_constraint("uq_run_project_pass", "runs", type_="unique")
    op.create_unique_constraint(
        "uq_run_suite_pass",
        "runs",
        [
            "suite_id",
            "solution_id",
            "dataset_version",
            "mode",
            "pass_idx",
            "parent_sweep_id",
            "sweep_arm",
        ],
        postgresql_nulls_not_distinct=True,
    )

    # Team-only RLS before the project columns go.
    bypass = "current_user_id() IS NULL"
    team_visible = (
        "EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = current_user_id() "
        "AND m.scope_kind = 'team' AND m.scope_id = {table}.team_id)"
    )
    for table in ("runs", "results", "verdicts", "traces", "attributions", "suites"):
        op.execute(f"DROP POLICY IF EXISTS {table}_visible ON {table};")
        op.execute(
            f"CREATE POLICY {table}_visible ON {table} FOR ALL "
            f"USING ({bypass} OR {team_visible.format(table=table)});"
        )
    op.execute("DROP POLICY IF EXISTS projects_visible ON projects;")
    # teams_visible reached through projects for project-scoped members; team
    # membership is the only path now.
    op.execute("DROP POLICY IF EXISTS teams_visible ON teams;")
    op.execute(
        "CREATE POLICY teams_visible ON teams FOR ALL USING ("
        "current_user_id() IS NULL OR EXISTS ("
        "SELECT 1 FROM memberships m WHERE m.user_id = current_user_id() "
        "AND m.scope_kind = 'team' AND m.scope_id = teams.id));"
    )
    # eval_item_suites' policy reaches through suites.project_id; rebuild it
    # on the suite's team alone.
    op.execute("DROP POLICY IF EXISTS eval_item_suites_visible ON eval_item_suites;")
    op.execute(
        "CREATE POLICY eval_item_suites_visible ON eval_item_suites FOR ALL USING ("
        "current_user_id() IS NULL OR EXISTS ("
        "SELECT 1 FROM suites s JOIN memberships m ON m.user_id = current_user_id() "
        "WHERE s.id = eval_item_suites.suite_id "
        "AND m.scope_kind = 'team' AND m.scope_id = s.team_id));"
    )

    for table in ("runs", "results", "verdicts", "traces", "attributions", "suites"):
        op.drop_column(table, "project_id")
    op.create_index(
        "idx_attributions_team_solution", "attributions", ["team_id", "solution_id", "suite"]
    )
    op.drop_table("project_solutions")
    connection.execute(sa.text("DELETE FROM memberships WHERE scope_kind = 'project'"))
    op.drop_table("projects")


def downgrade() -> None:
    """Not reversible: the project level is gone, not renamed."""
    raise NotImplementedError("0018 removes the Project level; restore from a backup instead")
