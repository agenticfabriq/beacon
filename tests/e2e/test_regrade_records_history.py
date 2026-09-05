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

import os
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from beacon_graders.graders.result_set_match import ResultSetMatchGrader
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.outcome_history import Derivation, ResultOutcome
from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
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


def _seed(session: Session, *, stored_outcome: VerdictOutcome, verdict_passes: bool) -> None:
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
        suite_metadata={"headline_metric": "exact_match"},
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
        gold_answer={"rows": [[1]], "columns": ["a"]},
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
        output={"rows": [[1]], "columns": ["a"]},
        output_kind="rows",
        tokens_input=None,
        tokens_output=None,
        runtime_ms=1,
        status=ResultStatus.COMPLETED,
        outcome=stored_outcome,
        error=None,
    )
    VerdictRepo(session).create(
        team_id=team.id,
        result_id=result.id,
        grader=grader.name,
        grader_version=grader.version,
        metric="exact_match",
        criterion="correctness",
        bool_value=verdict_passes,
        value=1.0 if verdict_passes else 0.0,
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
    _ = os.environ  # the script reads DATABASE_URL only as a fallback
