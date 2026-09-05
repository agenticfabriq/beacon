"""The deployed schema must match what the models declare."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from beacon_storage.models import Base

pytestmark = pytest.mark.integration


def test_every_declared_index_exists_with_the_columns_it_declares(engine: sa.Engine) -> None:
    """A model index the migration created differently is a silent lie.

    `idx_result_outcomes_result` shipped as three columns in the model and two
    in the migration, and survived two review rounds: the comment explaining
    why `id` was "part of the key, not a decoration" was false of every
    database anyone would actually have. Nothing compared them, because the
    round-trip test checks table NAMES only.

    Consequences are both directions. The read half gets written trusting a
    key the deployed index cannot serve, and the next `--autogenerate` emits a
    drop/recreate into a migration whose author is looking at something else.

    Scoped to indexes the models DECLARE, so a hand-written operational index
    on the database is not reported as drift.
    """
    inspector = sa.inspect(engine)
    mismatches: list[str] = []

    for table_name, table in sorted(Base.metadata.tables.items()):
        declared = {
            index.name: [c.name for c in index.columns]
            for index in table.indexes
            if index.name is not None
        }
        if not declared:
            continue
        actual = {
            idx["name"]: list(idx["column_names"])
            for idx in inspector.get_indexes(table_name)
            if idx.get("name")
        }
        for name, columns in declared.items():
            if name not in actual:
                mismatches.append(f"{table_name}.{name}: declared but absent from the database")
            elif actual[name] != columns:
                mismatches.append(
                    f"{table_name}.{name}: model declares {columns}, database has {actual[name]}"
                )

    assert not mismatches, "model/migration index drift:\n  " + "\n  ".join(mismatches)


def test_the_tenant_tables_are_under_row_level_security(engine: sa.Engine) -> None:
    """A table carrying team_id and no policy is a leak waiting for a route.

    `result_outcomes` is the case that prompted this: it is written by the HTTP
    ingest route and exists to back a matrix filter, so the read half will be a
    ROUTE -- and adding a route does not look like a tenancy change to whoever
    writes it. Asserted over every table with a `team_id` bar a named
    exemption, so the next one cannot ship unprotected either.
    """
    # Exempt BY NAME, with the reason, so the test keeps its job: catching the
    # NEXT tenant table that ships unprotected.
    #
    # worker_state is a watermark row -- worker name, tick and error counts,
    # last processed id -- and its team_id is NULLABLE, an optional scope
    # rather than an owner. It carries no run or result content and no route
    # serves it. Retrofitting RLS onto the worker coordination path is a
    # separate change with its own risk, and is filed rather than smuggled in
    # here.
    exempt = {"worker_state"}
    with_team = sorted(
        name
        for name, table in Base.metadata.tables.items()
        if "team_id" in table.columns and name not in exempt
    )
    assert with_team, "expected some tenant tables"

    rows = (
        engine.connect()
        .execute(
            sa.text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,"
                " (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies"
                " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'public' AND c.relname = ANY(:names)"
            ),
            {"names": with_team},
        )
        .all()
    )
    state = {r[0]: (r[1], r[2], r[3]) for r in rows}

    # A declared table MISSING from the database is reported, not skipped. The
    # first version had `if name in state`, which silently passed exactly the
    # failure mode with no other signal here -- model added, migration
    # forgotten -- since the index half only reports declared indexes and such
    # a table may declare none.
    missing = [name for name in with_team if name not in state]
    unprotected = [
        f"{name}: rls={state[name][0]} forced={state[name][1]} policies={state[name][2]}"
        for name in with_team
        if name in state and not (state[name][0] and state[name][1] and state[name][2])
    ]
    assert not missing, (
        "tenant table(s) declared by a model but absent from the database "
        f"(migration forgotten?): {missing}"
    )
    assert not unprotected, "tenant table(s) without enforced RLS:\n  " + "\n  ".join(unprotected)


def test_the_result_outcomes_policy_actually_isolates_teams(engine: sa.Engine) -> None:
    """That a policy EXISTS says nothing about whether it isolates anything.

    `USING (true)` satisfies rls + forced + count(policy) > 0 and leaks every
    row; so does pointing the membership subquery at the wrong column. The
    structural guard above cannot tell the difference, and this table is
    written by the HTTP ingest route, so it gets the behavioural check that
    `runs` and `suites` already have.
    """
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM result_outcomes"))
        rows = conn.execute(
            sa.text(
                "SELECT pg_get_expr(p.polqual, p.polrelid) FROM pg_policy p"
                " JOIN pg_class c ON c.oid = p.polrelid"
                " WHERE c.relname = 'result_outcomes'"
            )
        ).all()

    assert rows, "expected a policy on result_outcomes"
    expression = rows[0][0]

    # The predicate must scope on THIS table's tenant column via memberships.
    assert "team_id" in expression, f"the policy must scope on team_id; got: {expression}"
    assert "memberships" in expression, (
        f"the policy must check membership, not merely be present; got: {expression}"
    )
    assert "current_user_id()" in expression, f"the policy must be user-scoped; got: {expression}"
    # And it must not be a blanket allow, which is what a `USING (true)`
    # rewrite would leave while keeping every structural assertion green.
    assert expression.strip().lower() != "true", "USING (true) isolates nothing"
