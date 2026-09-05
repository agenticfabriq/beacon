"""Outcome history: recorded on change, and honest about what decided it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa
from beacon_storage.models.outcome_history import Derivation, ResultOutcome
from beacon_storage.repository.outcome_history import OutcomeHistoryRepo
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from beacon_storage.models.runs import Result
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _repo(session: Session) -> OutcomeHistoryRepo:
    return OutcomeHistoryRepo(session)


def test_the_no_grader_derivation_collapses_to_one_row(session: Session) -> None:
    """NULLS NOT DISTINCT, or every error push mints a fresh derivation.

    An error, a timeout, a refusal contract and a judge-only rubric all have
    no deciding grader. Postgres treats null as distinct in a unique index by
    default, so without NULLS NOT DISTINCT that one meaning would accumulate a
    row per push -- millions of rows saying the same thing.
    """
    first = _repo(session).derivation_for(grader=None, grader_version=None, metric=None)
    second = _repo(session).derivation_for(grader=None, grader_version=None, metric=None)
    session.commit()

    assert first.id == second.id
    assert session.scalar(sa.select(sa.func.count()).select_from(Derivation)) == 1


def test_a_derivation_is_interned_per_distinct_key(session: Session) -> None:
    repo = _repo(session)
    a = repo.derivation_for(grader="exec_sql", grader_version="v8", metric="exact_match")
    b = repo.derivation_for(grader="exec_sql", grader_version="v8", metric="exact_match")
    # Same grader and version, DIFFERENT metric: a different derivation, which
    # is the distinction B74 turned on -- 90 outcomes moved at a constant v8
    # because the headline metric changed.
    c = repo.derivation_for(grader="exec_sql", grader_version="v8", metric="got_facts")
    session.commit()

    assert a.id == b.id
    assert c.id != a.id
    assert session.scalar(sa.select(sa.func.count()).select_from(Derivation)) == 2


def test_a_metric_less_grader_is_a_valid_derivation(session: Session) -> None:
    """The case an earlier all-or-nothing rule would have crashed ingest on.

    A grader that declares no metric decides by being the first execution
    verdict, so its derivation genuinely names no metric. Verified reachable
    by construction against the composer before this rule was relaxed.
    """
    row = _repo(session).derivation_for(grader="exec_sql", grader_version="v1", metric=None)
    session.commit()

    assert row.grader == "exec_sql"
    assert row.metric is None


@pytest.mark.parametrize(
    ("grader", "version", "metric", "why"),
    [
        ("exec_sql", None, "m", "a grader with no version"),
        (None, "v1", None, "a version with no grader"),
        (None, None, "exact_match", "a metric with no grader to be a reading of"),
    ],
)
def test_an_incoherent_derivation_is_refused(
    session: Session, grader: str | None, version: str | None, metric: str | None, why: str
) -> None:
    with pytest.raises(ValueError, match="grader|metric"):
        _repo(session).derivation_for(grader=grader, grader_version=version, metric=metric)


def test_the_constraint_refuses_what_the_repo_refuses(session: Session) -> None:
    """The rule lives in the database too, not only in the code path.

    A second writer -- a script, a backfill, a future service -- does not go
    through the repo.
    """
    session.add(Derivation(grader=None, grader_version=None, metric="exact_match"))
    with pytest.raises(IntegrityError, match="ck_derivation_grader_and_version_together"):
        session.commit()
    session.rollback()


@pytest.fixture
def seeded_result(session: Session) -> Result:
    """One real result row: result_outcomes has FKs to both results and teams.

    Returns the ROW rather than its id so a test cannot pair a result with the
    wrong team -- which the FK would catch, but only after the test had already
    described something impossible.
    """
    from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
    from beacon_storage.models.suites import Suite
    from beacon_storage.models.tenancy import Team, User
    from beacon_storage.repository.results import ResultRepo
    from beacon_storage.repository.runs import RunRepo
    from beacon_storage.repository.solutions import SolutionRepo

    team = Team(name="history-team")
    user = User(email="history@example.com", name="H")
    session.add_all([team, user])
    session.flush()
    suite = Suite(
        team_id=team.id,
        name="history-suite",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )
    session.add(suite)
    session.flush()
    solution = SolutionRepo(session).create(
        team_id=team.id,
        solution_id="dummy",
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
        item_id="item-1",
        attempt_idx=0,
        output={"rows": []},
        output_kind="rows",
        tokens_input=None,
        tokens_output=None,
        runtime_ms=1,
        status=ResultStatus.COMPLETED,
        outcome=VerdictOutcome.FAIL,
        error=None,
    )
    session.flush()
    return result


def test_an_unchanged_outcome_is_not_recorded_twice(
    session: Session, seeded_result: Result
) -> None:
    """ON CHANGE is what keeps this proportional to what moved.

    Recording every derivation for every result would cost 127k rows per
    regrade for a value that is stable almost everywhere. The bird regrade of
    2026-09-04 touched 7,747 results and moved 298.
    """
    repo = _repo(session)
    first = repo.record(
        team_id=seeded_result.team_id,
        result_id=seeded_result.id,
        outcome="FAIL",
        source="ingest",
        grader="exec_sql",
        grader_version="v8",
        metric="exact_match",
    )
    session.flush()
    again = repo.record(
        team_id=seeded_result.team_id,
        result_id=seeded_result.id,
        outcome="FAIL",
        source="regrade",
        grader="exec_sql",
        grader_version="v8",
        metric="exact_match",
    )
    session.commit()

    assert first is not None
    assert again is None, "an unchanged outcome under the same derivation adds nothing"
    assert session.scalar(sa.select(sa.func.count()).select_from(ResultOutcome)) == 1


def test_the_same_outcome_under_a_NEW_derivation_is_recorded(
    session: Session, seeded_result: Result
) -> None:
    """A result can return to a value it held, under a different rule.

    That is a change worth recording even though the outcome string matches:
    it is the same answer for a different reason, and the derivation is what
    a later reader needs to reproduce the number.
    """
    repo = _repo(session)
    repo.record(
        team_id=seeded_result.team_id,
        result_id=seeded_result.id,
        outcome="PASS",
        source="ingest",
        grader="exec_sql",
        grader_version="v8",
        metric="exact_match",
    )
    session.flush()
    second = repo.record(
        team_id=seeded_result.team_id,
        result_id=seeded_result.id,
        outcome="PASS",
        source="regrade",
        grader="exec_sql",
        grader_version="v8",
        metric="got_facts",
    )
    session.commit()

    assert second is not None
    assert session.scalar(sa.select(sa.func.count()).select_from(ResultOutcome)) == 2


def test_a_changed_outcome_appends_rather_than_overwrites(
    session: Session, seeded_result: Result
) -> None:
    repo = _repo(session)
    for outcome in ("FAIL", "PASS", "FAIL"):
        repo.record(
            team_id=seeded_result.team_id,
            result_id=seeded_result.id,
            outcome=outcome,
            source="regrade",
            grader="exec_sql",
            grader_version="v8",
            metric="exact_match",
        )
        session.flush()
    session.commit()

    rows = list(
        session.scalars(
            sa.select(ResultOutcome)
            .where(ResultOutcome.result_id == seeded_result.id)
            .order_by(ResultOutcome.recorded_at, ResultOutcome.id)
        )
    )
    assert [r.outcome for r in rows] == ["FAIL", "PASS", "FAIL"], (
        "history is append-only: the value it held before must survive"
    )


def test_an_unknown_source_is_refused(session: Session, seeded_result: Result) -> None:
    """`source` distinguishes "changed in a regrade" from "changed at ingest".

    Those are different incidents, and the run-set gap means the second
    happens without any regrade at all.
    """
    with pytest.raises(ValueError, match="source"):
        _repo(session).record(
            team_id=seeded_result.team_id,
            result_id=seeded_result.id,
            outcome="PASS",
            source="manual-poke",
        )


def test_interning_survives_a_concurrent_insert(engine: sa.Engine, session: Session) -> None:
    """The race the harness found: the row appears between our SELECT and INSERT.

    Committing from another connection BEFORE the call does not reproduce it --
    the repo's SELECT simply hits, which is the easy path. The window is the
    one where our SELECT missed and someone else committed before our INSERT,
    so the miss is forced here while the row genuinely exists.

    The no-grader derivation is the one that races most: every error, timeout
    and refusal contract interns it, so two concurrent pushes is ordinary.
    """
    with engine.begin() as other:
        other.execute(
            sa.text(
                "INSERT INTO derivations (id, grader, grader_version, metric) "
                "VALUES (gen_random_uuid(), NULL, NULL, NULL) "
                "ON CONFLICT ON CONSTRAINT uq_derivation_key DO NOTHING"
            )
        )

    repo = _repo(session)
    real_scalar = session.scalar
    misses = {"left": 1}

    def scalar_missing_once(statement: Any, *args: Any, **kwargs: Any) -> Any:
        if misses["left"]:
            misses["left"] -= 1
            return None
        return real_scalar(statement, *args, **kwargs)

    session.scalar = scalar_missing_once  # type: ignore[method-assign]
    try:
        row = repo.derivation_for(grader=None, grader_version=None, metric=None)
    finally:
        session.scalar = real_scalar  # type: ignore[method-assign]

    assert row is not None, "interning must survive losing the race"
    assert session.scalar(sa.select(sa.func.count()).select_from(Derivation)) == 1, (
        "and must not have created a second row for the same derivation"
    )
