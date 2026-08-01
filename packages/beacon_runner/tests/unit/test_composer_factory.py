"""The composer a run uses comes from the suite's adapter, not a hardcoded list (B4)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from beacon_graders.graders import DabstepAnswerMatcher
from beacon_graders.types import GraderKind, VerdictOutcome
from beacon_runner.composer_factory import GraderRegistry, composer_for_suite, graders_for_suite
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep


def _sql_item() -> EvalItem:
    return EvalItem(
        item_id="i-0",
        suite="bird_minidev_v2",
        query={"question": "q"},
        ground_truth={"sql": "SELECT 1"},
        metadata={},
    )


def _sql_result() -> ExecutionResult:
    return ExecutionResult(
        output={"sql": "SELECT 1"},
        output_kind="sql",
        trace=ExecutionStep(uuid="r", name="r", level="workflow"),
    )


def test_a_sql_suite_resolves_its_execution_grader() -> None:
    """BIRD's adapter owns bird_minidev_v2 and registers the SQL grader."""
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        graders = graders_for_suite("bird_minidev_v2", engine=engine)
    finally:
        engine.dispose()

    assert [grader.name for grader in graders] == ["bird_minidev_v2.exec_sql"]
    assert graders[0].kind is GraderKind.EXECUTION


def test_a_sql_suite_composes_pass_rather_than_error() -> None:
    """The old hardcoded answer-matcher never applied to SQL, so items ERRORed."""
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        composer = composer_for_suite("bird_minidev_v2", engine=engine)
        _, outcome = composer.compose(_sql_item(), _sql_result())
    finally:
        engine.dispose()

    assert outcome is VerdictOutcome.PASS


def test_the_old_default_composer_would_have_errored_this_item() -> None:
    """Pins why the fallback alone is not enough for a SQL suite."""
    from beacon_graders.composer import VerdictComposer

    composer = VerdictComposer(graders=[DabstepAnswerMatcher()])
    _, outcome = composer.compose(_sql_item(), _sql_result())

    assert outcome is VerdictOutcome.ERROR


def test_an_unclaimed_suite_falls_back() -> None:
    fallback = [DabstepAnswerMatcher()]
    composer = composer_for_suite("no_such_suite_v1", fallback=fallback)

    assert [grader.name for grader in composer.graders] == ["dabstep_answer_matcher"]


def test_an_execution_suite_without_a_database_says_so() -> None:
    """The failure names the missing flag instead of surfacing a driver error."""
    registry = GraderRegistry(engine=None)

    with pytest.raises(ValueError, match="--benchmark-db-url"):
        registry.engine_factory(_sql_item())
