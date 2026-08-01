"""The SQL grader bounds arbitrary model-generated SQL (B6).

The knob existed and was read by nothing, so a pathological candidate query
held a harness worker with its DB connection pinned until the per-item
timeout -- on a shared benchmark engine that serialises the whole run.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from beacon_graders.graders import ExecutionGroundedSqlGrader
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_engine() -> Iterator[sa.Engine]:
    url = os.environ.get("DATABASE_URL")
    if not url or "postgresql" not in url:
        pytest.skip("needs a PostgreSQL DATABASE_URL")
    engine = sa.create_engine(url)
    yield engine
    engine.dispose()


def _item() -> EvalItem:
    return EvalItem(
        item_id="slow-1",
        suite="s",
        query={"question": "q"},
        ground_truth={"sql": "SELECT 1"},
        metadata={},
    )


def _result(sql: str) -> ExecutionResult:
    return ExecutionResult(
        output={"sql": sql},
        output_kind="sql",
        trace=ExecutionStep(uuid="r", name="r", level="workflow"),
    )


def test_a_slow_candidate_query_is_cancelled(pg_engine: sa.Engine) -> None:
    """A candidate that would run for 30s is stopped in about one."""
    grader = ExecutionGroundedSqlGrader(
        engine_factory=lambda _item: pg_engine,
        statement_timeout_seconds=1,
    )

    started = time.monotonic()
    verdicts = grader.grade(_item(), _result("SELECT pg_sleep(30)"))
    elapsed = time.monotonic() - started

    assert elapsed < 15, f"query ran {elapsed:.1f}s; the timeout did not fire"
    assert len(verdicts) == 1
    assert verdicts[0].bool_value is False
    assert "Candidate SQL failed" in verdicts[0].justification


def test_the_timeout_does_not_leak_to_the_next_statement(pg_engine: sa.Engine) -> None:
    """SET LOCAL is transaction-scoped, so a pooled connection is not poisoned."""
    grader = ExecutionGroundedSqlGrader(
        engine_factory=lambda _item: pg_engine,
        statement_timeout_seconds=1,
    )
    grader.grade(_item(), _result("SELECT pg_sleep(30)"))

    with pg_engine.connect() as connection:
        value = connection.exec_driver_sql("SHOW statement_timeout").scalar()
    assert value in ("0", "0ms"), f"statement_timeout leaked as {value!r}"


def test_a_normal_query_is_unaffected(pg_engine: sa.Engine) -> None:
    grader = ExecutionGroundedSqlGrader(
        engine_factory=lambda _item: pg_engine,
        statement_timeout_seconds=30,
    )

    verdicts = grader.grade(_item(), _result("SELECT 1"))

    assert verdicts[0].bool_value is True


def test_zero_disables_the_bound() -> None:
    grader = ExecutionGroundedSqlGrader(
        engine_factory=lambda _item: sa.create_engine("sqlite+pysqlite:///:memory:"),
        statement_timeout_seconds=0,
    )
    assert grader.statement_timeout_seconds == 0


def test_sqlite_has_no_server_side_statement_timeout() -> None:
    """Documented gap: the bound is a no-op for dialects without one."""
    assert ExecutionGroundedSqlGrader.supports_statement_timeout("postgresql") is True
    assert ExecutionGroundedSqlGrader.supports_statement_timeout("sqlite") is False
