"""The fs payments loader: what beacon stores must survive being stored."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path  # noqa: TC003 -- pytest resolves tmp_path hints at runtime
from uuid import uuid4

import duckdb
from beacon_benchmarks.fs_payments import (
    DATASET_VERSION,
    HEADLINE_METRIC,
    SUITE,
    execute_gold,
    ingest_fs_payments_tasks,
    load_tasks,
)
from beacon_benchmarks.fs_payments.ingest_items import _json_safe


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


class _FakeItem:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeItemsRepo:
    """Enough of the repo to run the ingest. Its whole job is to make the function EXECUTE."""

    def __init__(self):
        self.upserted: list[dict] = []
        self.versions: list[dict] = []

    def upsert_by_question_hash(self, **kwargs):
        self.upserted.append(kwargs)
        return _FakeItem(item_id=uuid4(), valid_from=datetime.now(UTC), solution_id=None,
                         item_input=kwargs["item_input"], gold_answer=kwargs["gold_answer"],
                         item_metadata=kwargs["item_metadata"],
                         dataset_version=kwargs["dataset_version"]), True

    def set_valid_to(self, **kwargs):  # pragma: no cover - only on a refresh
        self.versions.append(kwargs)

    def insert_new_version(self, **kwargs):  # pragma: no cover - only on a refresh
        self.versions.append(kwargs)


def test_the_ingest_actually_runs(tmp_path):
    """B1: the lazy `beacon_storage` import was singular where the module is plural, and twelve
    green tests never caught it because none of them called this function. A test that executes it
    makes the whole defect class uncatchable-by-accident -- the import runs either way."""
    database = tmp_path / "corpus.duckdb"
    con = duckdb.connect(str(database))
    con.execute("create table t (v integer)")
    con.execute("insert into t values (1), (2)")
    con.close()

    gold = _gold(tmp_path, [
        {"id": "a", "question": "sum?", "gold_sql": "select sum(v) as s from t",
         "answerable": True, "tags": ["meaning", "revenue"]},
        {"id": "b", "question": "which merchants churned?", "gold_sql": None,
         "answerable": False, "tags": ["refusal", "unanswerable"]},
    ])
    repo = _FakeItemsRepo()

    result = ingest_fs_payments_tasks(
        gold_path=gold, database_path=database, items_repo=repo,
        team_id=uuid4(), created_by=uuid4(),
    )

    assert result.inserted == 2
    answerable, unanswerable = repo.upserted
    assert answerable["gold_answer"]["accepted_results"] == [{"columns": ["s"], "rows": [[3]]}]
    assert unanswerable["gold_answer"]["accepted_results"] == []
    assert unanswerable["item_input"]["answerable"] is False
    # S1/S2: neither declaration is copied onto an item.
    assert "headline_metric" not in answerable["item_metadata"]
    assert "tolerance" not in answerable["item_metadata"]
    assert json.dumps(answerable["gold_answer"]), "everything stored must survive JSONB"


def test_the_suite_declares_its_headline_metric():
    """Every scored outcome derives from beacon's verdicts under a declared rule."""
    assert HEADLINE_METRIC == "exact_match"
    assert SUITE == "fs_payments_v1"  # underscored + versioned, like its siblings
    assert DATASET_VERSION == "fs-payments-v1"
