"""The regrade loop, executed -- not parsed.

Every other test of `regrade_suite.py` is AST-structural, which is why the
round-1 defect survived: `history.record` sat inside the flip branch, so a
regrade re-attributed only the outcomes that MOVED and left every other
re-derived result crediting the previous derivation. On the bird regrade that
was 7,449 of 7,747, and a read half filtering by derivation would have
computed over 298 -- the partial denominator the whole table exists to
prevent. Moving either call back inside its branch passes the structural
suite untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from beacon_graders.graders.result_set_match import ResultSetMatchGrader
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.outcome_history import Derivation, ResultOutcome
from beacon_storage.models.runs import HarnessMode, Result, ResultStatus, Verdict, VerdictOutcome
from beacon_storage.models.suites import Suite
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.repository.verdicts import VerdictRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.e2e

_SUITE = "regrade_history_suite"


def _seed(
    session: Session,
    *,
    stored_outcome: VerdictOutcome,
    verdict_passes: bool | None,
    with_current_verdict: bool = True,
    headline: str = "exact_match",
    gold: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """One result whose stored outcome may or may not match its verdict.

    The verdict is written at the CURRENT grader version and headline metric,
    so the regrade takes its already-current branch and re-derives from it --
    the branch where the round-1 defect lived.
    """
    grader = ResultSetMatchGrader()
    user = UserRepo(session).create(email="rg@example.com", name="R")
    team = TeamRepo(session).create(name="rg-team")
    suite = Suite(
        team_id=team.id,
        name=_SUITE,
        description="",
        method="manual",
        suite_metadata={"headline_metric": headline},
        created_by=user.id,
    )
    session.add(suite)
    session.flush()
    # Through the repo: EvalItem is bitemporal and `valid_from` is part of its
    # primary key, so a hand-built row has no valid version to be found by.
    item = EvalItemRepo(session).create(
        tier=EvalItemTier.HUMAN_VERIFIED,
        suite=_SUITE,
        team_id=team.id,
        item_input={"question": "q"},
        gold_answer=gold or {"rows": [[1]], "columns": ["a"]},
        item_metadata={},
        created_by=user.id,
    )
    solution = SolutionRepo(session).create(
        team_id=team.id,
        solution_id="rg-sut",
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
        suite=_SUITE,
        dataset_version="v0",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={},
        created_by=user.id,
    )
    result = ResultRepo(session).create(
        team_id=team.id,
        run_id=run.id,
        item_id=str(item.item_id),
        attempt_idx=0,
        output=output or {"rows": [[1]], "columns": ["a"]},
        # "sql": ResultSetMatchGrader.applicable requires it, so "rows" made
        # the grader inapplicable and the freshly-graded branch never ran.
        output_kind="sql",
        tokens_input=None,
        tokens_output=None,
        runtime_ms=1,
        status=ResultStatus.COMPLETED,
        outcome=stored_outcome,
        error=error,
    )
    if with_current_verdict:
        VerdictRepo(session).create(
            team_id=team.id,
            result_id=result.id,
            grader=grader.name,
            grader_version=grader.version,
            metric=headline,
            criterion="correctness",
            bool_value=verdict_passes,
            value=None if verdict_passes is None else 1.0 if verdict_passes else 0.0,
            justification="seeded",
            raw_output={},
        )
    session.commit()


def _run_regrade(db_url: str) -> int:
    from scripts.regrade_suite import main

    argv = ["regrade_suite.py", "--suite", _SUITE, "--database-url", db_url]
    with patch("sys.argv", argv):
        return main()


def test_a_result_whose_outcome_does_not_move_is_still_attributed(
    session: Session, db_url: str
) -> None:
    """The round-1 defect, executed.

    The stored outcome already agrees with the verdict, so nothing flips --
    and the derivation must still be recorded, or this result's history keeps
    crediting whatever rule produced it before.
    """
    _seed(session, stored_outcome=VerdictOutcome.PASS, verdict_passes=True)

    assert _run_regrade(db_url) == 0

    rows = list(session.scalars(sa.select(ResultOutcome)))
    assert len(rows) == 1, (
        "a re-derivation that changes no outcome must still be attributed; "
        "recording only flips leaves the history crediting the previous rule"
    )
    assert rows[0].source == "regrade"
    derivation = session.get(Derivation, rows[0].derivation_id)
    assert derivation is not None
    assert derivation.metric == "exact_match", "the headline the regrade derived under"
    assert derivation.grader_version == ResultSetMatchGrader().version


def test_a_flipped_outcome_is_recorded_with_its_new_value(session: Session, db_url: str) -> None:
    """And the moving case still works, with the new value not the old."""
    _seed(session, stored_outcome=VerdictOutcome.FAIL, verdict_passes=True)

    assert _run_regrade(db_url) == 0

    rows = list(session.scalars(sa.select(ResultOutcome)))
    assert [r.outcome for r in rows] == ["PASS"]


def test_a_second_regrade_records_nothing_new(session: Session, db_url: str) -> None:
    """On-change means idempotent: the same rule twice adds one row, not two."""
    _seed(session, stored_outcome=VerdictOutcome.FAIL, verdict_passes=True)

    assert _run_regrade(db_url) == 0
    assert _run_regrade(db_url) == 0

    assert len(list(session.scalars(sa.select(ResultOutcome)))) == 1, (
        "an unchanged outcome under an unchanged derivation must not append"
    )


def test_the_freshly_graded_branch_also_attributes_every_result(
    session: Session, db_url: str
) -> None:
    """The OTHER record call, which the first three tests never reached.

    They all seeded a verdict at the current grader version, so the regrade
    took its already-current branch and `continue`d -- the freshly-graded path
    was never entered, and moving ITS record call inside the flip check
    reproduced the round-1 defect with every test green.

    Seeded with no verdict at all, so the grader runs for real. The stored
    outcome already agrees with what it will conclude, so nothing flips and the
    attribution is the only thing being asserted.
    """
    _seed(
        session,
        stored_outcome=VerdictOutcome.PASS,
        verdict_passes=True,
        with_current_verdict=False,
    )

    assert _run_regrade(db_url) == 0

    rows = list(session.scalars(sa.select(ResultOutcome)))
    assert len(rows) == 1, (
        "a freshly graded result whose outcome does not move must still be "
        "attributed to the derivation that produced it"
    )
    derivation = session.get(Derivation, rows[0].derivation_id)
    assert derivation is not None
    assert derivation.metric == "exact_match"
    assert derivation.grader_version == ResultSetMatchGrader().version


# Every column holds both flags, so every pair survives the got-facts pruning and none holds
# gold's rows: at a budget of 3 projections the search cannot decide; at the shipped budget it
# decides False. Exact match is False either way.
_UNDECIDABLE = {
    "gold": {"rows": [[True, False], [False, True]], "columns": ["a", "b"]},
    "output": {"rows": [[True] * 8, [False] * 8], "columns": [f"c{i}" for i in range(8)]},
}


def _outcome(session: Session) -> str:
    session.expire_all()
    return str(session.scalar(sa.select(Result.outcome)))


def _regrade_undecided_to_error(session: Session, db_url: str) -> None:
    """A FAIL graded at a budget too small to decide got-facts: ERROR, not FAIL."""
    _seed(
        session,
        stored_outcome=VerdictOutcome.FAIL,
        verdict_passes=False,
        with_current_verdict=False,
        headline="got_facts",
        **_UNDECIDABLE,
    )
    with patch("beacon_graders.comparison.MAX_CHOICES", 3):
        assert _run_regrade(db_url) == 0
    assert _outcome(session) == "ERROR"
    facts = session.scalar(sa.select(Verdict).where(Verdict.metric == "got_facts"))
    assert facts is not None
    assert facts.bool_value is None


def test_an_undecided_headline_regrades_to_ERROR_and_a_later_version_restates_it(
    session: Session, db_url: str
) -> None:
    """PASS/FAIL -> undecided ERROR -> decided again. A regrade never re-derived an ERROR, so
    the first undecided grade was final: a later grader version that could decide never reached
    it. The ERROR is the grader's own (no runner error, an undecided verdict beside it), so the
    next version's decided verdict restates it."""
    _regrade_undecided_to_error(session, db_url)

    with patch.object(ResultSetMatchGrader, "version", "v-next"):
        assert _run_regrade(db_url) == 0

    assert _outcome(session) == "FAIL"


def test_an_undecided_ERROR_is_restated_when_the_headline_moves_to_a_decided_metric(
    session: Session, db_url: str
) -> None:
    """The same ERROR, reached through the already-current branch: the suite switches its
    headline to exact_match, which was decided all along, at the same grader version."""
    _regrade_undecided_to_error(session, db_url)
    suite = session.scalar(sa.select(Suite).where(Suite.name == _SUITE))
    assert suite is not None
    suite.suite_metadata = {"headline_metric": "exact_match"}
    session.commit()

    assert _run_regrade(db_url) == 0

    assert _outcome(session) == "FAIL"


def test_a_still_undecided_grade_stays_ERROR(session: Session, db_url: str) -> None:
    """Restatable is not restated: a regrade at the same version re-derives ERROR."""
    _regrade_undecided_to_error(session, db_url)
    with patch("beacon_graders.comparison.MAX_CHOICES", 3):
        assert _run_regrade(db_url) == 0
    assert _outcome(session) == "ERROR"


def test_the_runners_ERROR_is_never_restated(session: Session, db_url: str) -> None:
    """An ERROR the runner reported keeps its error text on the result, and stays ERROR even
    though the pushed rows would grade: whether a query was produced is not the grader's call."""
    _seed(
        session,
        stored_outcome=VerdictOutcome.ERROR,
        verdict_passes=False,
        with_current_verdict=False,
        headline="got_facts",
        error="connection refused",
        **_UNDECIDABLE,
    )
    # First an undecided verdict lands beside it, then a version that decides: the verdict alone
    # must not make the runner's ERROR look like the grader's.
    with patch("beacon_graders.comparison.MAX_CHOICES", 3):
        assert _run_regrade(db_url) == 0
    assert _outcome(session) == "ERROR"
    with patch.object(ResultSetMatchGrader, "version", "v-next"):
        assert _run_regrade(db_url) == 0
    assert _outcome(session) == "ERROR"


def test_an_undecided_verdict_already_at_this_version_derives_ERROR(
    session: Session, db_url: str
) -> None:
    """The already-current branch read a stored undecided headline as "skip", leaving whatever an
    earlier rule had written -- here a PASS -- beside a verdict that says it could not decide.
    It derives the way the freshly graded branch does."""
    _seed(
        session,
        stored_outcome=VerdictOutcome.PASS,
        verdict_passes=None,
        headline="got_facts",
        **_UNDECIDABLE,
    )
    assert _run_regrade(db_url) == 0
    assert _outcome(session) == "ERROR"
