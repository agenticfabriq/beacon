"""The fs payments loader: what beacon stores must survive being stored."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from beacon_benchmarks.fs_payments import (
    DATASET_VERSION,
    HEADLINE_METRIC,
    SUITE,
    execute_gold,
    load_tasks,
)
from beacon_benchmarks.fs_payments.ingest_items import _json_safe, _row_order_insensitive


def _gold(tmp_path: Path, cases: list[dict]) -> Path:
    path = tmp_path / "gold.json"
    path.write_text(json.dumps(cases))
    return path


def test_gold_rows_survive_jsonb():
    """Measured against the real corpus before this existed: 10 of 24 gold results failed
    `json.dumps` outright. DuckDB hands back Decimal for money and datetime for instants, and JSONB
    holds neither."""
    assert json.dumps(_json_safe(Decimal("623780.61"))) == "623780.61"
    assert _json_safe(datetime(2026, 6, 30, 23, 46, 31)) == "2026-06-30T23:46:31"
    # Nothing else is coerced: values_match is type-strict, so a textual code stays textual.
    assert _json_safe("SETTLED") == "SETTLED"
    assert _json_safe(20000) == 20000


def test_gold_carries_its_column_order(tmp_path):
    """Column order is part of exact match and dict rows carry theirs only in flight, so the
    ordered array is stamped at ingest. A regrade refuses evidence without one."""
    con = duckdb.connect()
    con.execute("create table t (b integer, a integer)")
    con.execute("insert into t values (1, 2)")
    tasks = load_tasks(
        _gold(tmp_path, [{"id": "x", "question": "q", "gold_sql": "select b, a from t",
                          "answerable": True, "tags": ["meaning", "trap"]}])
    )

    table = execute_gold(tasks[0], con)

    assert table["columns"] == ["b", "a"], "the query's order, not the table's"
    assert table["rows"] == [[1, 2]]


def test_an_unanswerable_case_carries_no_gold(tmp_path):
    """A correct deferral is the runner's statement. The loader must not manufacture an empty gold
    for it, or a missing verdict starts to look like a failed one."""
    tasks = load_tasks(
        _gold(tmp_path, [{"id": "r", "question": "which merchants churned?", "gold_sql": None,
                          "answerable": False, "tags": ["refusal", "unanswerable"]}])
    )

    assert tasks[0].answerable is False
    assert execute_gold(tasks[0], duckdb.connect()) is None


@pytest.mark.parametrize(
    ("sql", "insensitive"),
    [
        ("select payment_channel, sum(v) from t group by 1 order by 1", False),
        ("select sum(v) from t", True),
    ],
)
def test_row_order_opinion_is_curated_not_inferred(tmp_path, sql, insensitive):
    """Curated opinion outranks the ORDER BY heuristic at grading, so the loader states it."""
    tasks = load_tasks(
        _gold(tmp_path, [{"id": "x", "question": "q", "gold_sql": sql, "answerable": True,
                          "tags": ["meaning"]}])
    )
    assert _row_order_insensitive(tasks[0]) is insensitive


def test_the_suite_declares_its_headline_metric():
    """Every scored outcome derives from beacon's verdicts under a declared rule."""
    assert HEADLINE_METRIC == "exact_match"
    assert SUITE == "fs-payments"
    assert DATASET_VERSION == "fs-payments-v1"
