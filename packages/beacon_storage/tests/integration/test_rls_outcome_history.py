"""result_outcomes must not be readable across teams."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from beacon_storage.db import make_session_factory
from beacon_storage.models.outcome_history import ResultOutcome
from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
from beacon_storage.models.suites import Suite
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.outcome_history import OutcomeHistoryRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from sqlalchemy import text

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _use_app_role(session: Session) -> None:
    session.execute(text("SET LOCAL ROLE beacon_app"))


def _seed_team_with_history(session: Session, *, who: str) -> tuple[UUID, UUID]:
    """One team with one recorded outcome. Returns (user_id, team_id)."""
    user = UserRepo(session).create(email=f"{who}@example.com", name=who)
    team = TeamRepo(session).create(name=f"{who}-team")
    # The policy is membership-based, so a team without one sees nothing --
    # which is the correct default and was the first version's bug here.
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team.id,
        role=Role.TEAM_ADMIN,
    )
    suite = Suite(
        team_id=team.id,
        name=f"{who}-suite",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )
    session.add(suite)
    session.flush()
    solution = SolutionRepo(session).create(
        team_id=team.id,
        solution_id=f"{who}-sut",
        version="0.1.0",
        owner_team=team.id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=user.id,
    )
    run = RunRepo(session).create(
        team_id=team.id,
        suite_id=suite.id,
        solution_id=solution.id,
        suite=suite.name,
        dataset_version="v0",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={},
        created_by=user.id,
    )
    result = ResultRepo(session).create(
        team_id=team.id,
        run_id=run.id,
        item_id="i-1",
        attempt_idx=0,
        output={"rows": []},
        output_kind="rows",
        tokens_input=None,
        tokens_output=None,
        runtime_ms=1,
        status=ResultStatus.COMPLETED,
        outcome=VerdictOutcome.PASS,
        error=None,
    )
    OutcomeHistoryRepo(session).record(
        team_id=team.id,
        result_id=result.id,
        outcome="PASS",
        source="ingest",
        grader="exec_sql",
        grader_version="v8",
        metric="exact_match",
    )
    return user.id, team.id


def test_outcome_history_is_not_readable_across_teams(engine: Engine) -> None:
    """Behavioural, not textual: the rows are queried as each user.

    What this DOES catch is a policy that stops isolating -- `USING (true)`,
    or one scoped on the wrong column -- which a structural "a policy exists"
    check and a substring check both pass. That is why `runs` and `suites` are
    tested this way.

    What it does NOT catch is deleting `m.user_id = current_user_id()` from
    the predicate, and saying so here matters: measured, alice still sees 1
    and bob 1, because `memberships` is itself under FORCE RLS so the subquery
    only ever sees the caller's own rows. A maintainer who deletes that clause
    and sees green should conclude the clause is redundant defence in depth --
    which it is -- and NOT that this test is broken. See the sibling test for
    the coupling that actually carries the isolation.
    """
    factory = make_session_factory(engine)

    with factory() as setup:
        alice_id, _alice_team = _seed_team_with_history(setup, who="alice")
        bob_id, _bob_team = _seed_team_with_history(setup, who="bob")
        setup.commit()

    with factory() as session:
        _use_app_role(session)
        set_current_user(session, alice_id)
        mine = list(session.scalars(sa.select(ResultOutcome)))
        assert len(mine) == 1, "a member sees their own team's history"

    with factory() as session:
        _use_app_role(session)
        set_current_user(session, bob_id)
        theirs = list(session.scalars(sa.select(ResultOutcome)))
        assert len(theirs) == 1, "and exactly one -- not alice's as well"

    with factory() as session:
        _use_app_role(session)
        # A user with no membership anywhere sees nothing, which is the case a
        # membership-without-user-check predicate would leak.
        nobody = UserRepo(session).create(email="nobody@example.com", name="N")
        session.flush()
        set_current_user(session, nobody.id)
        assert list(session.scalars(sa.select(ResultOutcome))) == []


def test_memberships_rls_is_load_bearing_for_this_table(engine: Engine) -> None:
    """This table's isolation depends on ANOTHER table's policy.

    `result_outcomes_visible` scopes through
    `EXISTS (SELECT 1 FROM memberships ...)`, and `memberships` is itself
    under FORCE RLS -- so the subquery only ever sees the caller's own
    membership rows. Measured: deleting `m.user_id = current_user_id()` from
    the policy changes no behaviour at all, because membership rows for other
    users are already invisible.

    The clause stays as defence in depth, but the real dependency is this one,
    and it is invisible from 0023: relaxing memberships' RLS would open this
    table without anyone editing its migration. So it is asserted here, where
    a reader of this table's tests will see it.
    """
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT relrowsecurity, relforcerowsecurity,"
                " (SELECT count(*) FROM pg_policy p WHERE p.polrelid = cl.oid)"
                " FROM pg_class cl JOIN pg_namespace n ON n.oid = cl.relnamespace"
                " WHERE n.nspname = 'public' AND cl.relname = 'memberships'"
            )
        ).one()

    enabled, forced, policies = row
    assert enabled and forced and policies, (
        "memberships must stay under enforced RLS: result_outcomes' policy "
        f"scopes through it (rls={enabled} forced={forced} policies={policies})"
    )

    # The FLAGS are not enough, and checking only them was the first version's
    # weakness: replacing memberships' policy with `USING (true)` leaves
    # rls/forced/count untouched while making every membership row readable --
    # and with the defence-in-depth clause also gone the leak is complete
    # (measured: alice sees 2 of 2). So the predicate is read too.
    with engine.connect() as conn:
        # Schema-qualified like the flags query above, and NULL predicates
        # excluded: pg_get_expr returns NULL for a policy with no USING clause
        # (a FOR INSERT policy has only WITH CHECK), which would raise an
        # AttributeError below instead of asserting.
        predicates = [
            r[0]
            for r in conn.execute(
                sa.text(
                    "SELECT pg_get_expr(p.polqual, p.polrelid) FROM pg_policy p"
                    " JOIN pg_class c ON c.oid = p.polrelid"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND c.relname = 'memberships'"
                    " AND p.polqual IS NOT NULL"
                )
            ).all()
        ]

    assert predicates, "expected a predicate on memberships' policy"
    for predicate in predicates:
        assert predicate.strip().lower() != "true", (
            "memberships' policy must not be a blanket allow: result_outcomes "
            "isolation is scoped through it"
        )
        assert "current_user_id()" in predicate, (
            f"memberships must stay user-scoped; got: {predicate}"
        )
