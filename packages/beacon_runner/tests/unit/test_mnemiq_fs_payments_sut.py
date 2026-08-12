"""The grounded arm must be unable to silently equal baseline.

Every assertion here corresponds to a way that actually happened while building the corpus, not to
a hypothetical. A gate that has never been shown to fire is decoration.
"""

from __future__ import annotations

import json
from collections.abc import Sequence  # noqa: TC003 -- resolved at runtime with the hints below
from pathlib import Path  # noqa: TC003 -- pytest resolves tmp_path hints at runtime
from uuid import uuid4

import pytest
from beacon_runner.sut.mnemiq.fs_payments import (
    CertifiedRecordsNotGrounded,
    MnemiqFsPaymentsSUT,
)
from beacon_runner.types import EvalItem


class _Snapshot:
    def __init__(
        self,
        definitions: Sequence[object] = (),
        metrics: Sequence[object] = (),
        dimensions: Sequence[object] = (),
    ) -> None:
        self.definitions = list(definitions)
        self.metrics = list(metrics)
        self.dimensions = list(dimensions)


def _sut(tmp_path: Path, *, expect: int = 38, reaches: int = 1) -> MnemiqFsPaymentsSUT:
    """The packet probe is injected, the way the base class injects its engine builder.

    Beacon's CI has no mnemiq, and the default probe imports it. A gate that can only be shown to
    fire on a machine with mnemiq installed is not demonstrably a gate, so the gate LOGIC is tested
    against a stub and the mnemiq-backed probe stays the default in production.
    """
    records = tmp_path / "certified_records.json"
    records.write_text(json.dumps({"records": []}))
    return MnemiqFsPaymentsSUT(
        owner_team_id=uuid4(),
        database_path=str(tmp_path / "corpus.duckdb"),
        records_url=f"file://{records}",
        enrich_cache_dir=str(tmp_path / "cache"),
        expect_records=expect,
        packet_probe=lambda _snapshot: reaches,
    )


def test_a_short_fetch_is_refused(tmp_path: Path) -> None:
    """The fail-soft 401. `fetch_certified_records` returns [] on any transport failure so a Verity
    outage cannot brick mnemiq — correct for the product, fatal for an experiment, because both
    arms then produce an identical prompt and the run reports no effect."""
    sut = _sut(tmp_path)

    with pytest.raises(CertifiedRecordsNotGrounded, match="fetched 0 certified records"):
        sut._assert_grounded(_Snapshot(), [], before=0)


def test_an_empty_incremental_delta_is_refused(tmp_path: Path) -> None:
    """The watermark. A successful pull stamps one; the next sends `?since=` and receives nothing.
    An eval whose second run grounds on less than its first, with nothing saying so."""
    sut = _sut(tmp_path, expect=38)

    with pytest.raises(CertifiedRecordsNotGrounded, match="expected 38"):
        sut._assert_grounded(_Snapshot(), [object()], before=0)


def test_records_that_never_reach_the_packet_are_refused(tmp_path: Path) -> None:
    """The third way, and the one a snapshot-level check misses: certified metrics and dimensions
    sat in `snapshot.metrics` read by nothing for as long as they existed."""
    # Applied to the snapshot -- `before` was 0 and it now holds one -- but selected into the
    # packet by nothing, which is exactly where certified metrics and dimensions sat.
    sut = _sut(tmp_path, expect=1, reaches=0)

    with pytest.raises(CertifiedRecordsNotGrounded, match="retrieval packet"):
        sut._assert_grounded(_Snapshot(definitions=[object()]), [object()], before=0)


def test_a_grounded_arm_that_grounded_passes(tmp_path: Path) -> None:
    """Non-vacuity: the gate must not refuse everything. Records arrived, the snapshot grew, and
    something reached the packet -- what grounding looks like when it worked."""
    sut = _sut(tmp_path, expect=1, reaches=3)

    sut._assert_grounded(_Snapshot(definitions=[object()]), [object()], before=0)


def test_run_config_records_the_snapshot_it_read(tmp_path: Path) -> None:
    """Two checkouts carrying the same basename at different states is the same trap as two
    databases with the same name, so the digest is of the bytes actually read."""
    sut = _sut(tmp_path)

    config = sut.run_config()

    assert config["records_sha256"] is not None, "a local snapshot must carry its digest"
    assert config["expect_records"] == 38
    assert str(config["verity_records_url"]).startswith("file://")


def test_a_remote_records_url_admits_it_has_no_digest(tmp_path: Path) -> None:
    """Claiming a digest for something not read from disk would be worse than admitting there is
    none."""
    sut = MnemiqFsPaymentsSUT(
        owner_team_id=uuid4(),
        database_path=str(tmp_path / "corpus.duckdb"),
        records_url="https://verity.example/api/semantic/records/open",
        enrich_cache_dir=str(tmp_path / "cache"),
        expect_records=38,
    )

    assert sut.run_config()["records_sha256"] is None


# --- The SUT must push the rows it executed, not just the SQL -----------------------------------
#
# The first real sweep returned ERROR on 24 of 29 items in BOTH arms and reported delta 0.0,
# p = 1.0. Nothing was wrong with the answers: one of those "errors" was pulled out of the results
# table and executed fine against the corpus, returning 12 rows, having handled the CDC revision
# trap correctly. `ResultSetMatchGrader.applicable()` requires `output["rows"]`, the base SUT emits
# `{"sql": ..., "item_id": ...}`, so the grader never applied and the composer scored every
# non-deferred item ERROR.
#
# The BIRD SUT gets away with emitting SQL because beacon executes it against the benchmark
# Postgres. This corpus is a DuckDB file only the SUT holds a handle on, so only the SUT can turn
# an answer into a result set.


class _Trace:
    def __init__(self, sql: str) -> None:
        self.target_sql = sql
        self.enrichment_version = "v1"


class _Answer:
    def __init__(self, sql: str, *, deferred: bool = False, failed: bool = False) -> None:
        self.answer = "answer text"
        self.trace = _Trace(sql)
        self.deferred = deferred
        self.failed = failed
        self.reason_code = None
        self.cached = False


def _corpus(tmp_path: Path) -> Path:
    import duckdb

    path = tmp_path / "corpus.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "create table payment_transaction (channel varchar, amount decimal(12,2), day date)"
    )
    con.execute("insert into payment_transaction values ('IN_STORE', 40505.25, date '2026-01-02')")
    con.execute("insert into payment_transaction values ('ONLINE', 12.50, date '2026-01-03')")
    con.close()
    return path


def _item() -> EvalItem:
    return EvalItem(item_id="i1", suite="fs_payments_v1", query={"question": "q"})


def _sut_over(corpus: Path, tmp_path: Path) -> MnemiqFsPaymentsSUT:
    records = tmp_path / "certified_records.json"
    records.write_text(json.dumps({"records": []}))
    return MnemiqFsPaymentsSUT(
        owner_team_id=uuid4(),
        database_path=str(corpus),
        records_url=f"file://{records}",
        enrich_cache_dir=str(tmp_path / "cache"),
        expect_records=0,
        packet_probe=lambda _s: 1,
    )


def test_an_answered_item_pushes_the_rows_it_executed(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    sut = _sut_over(corpus, tmp_path)

    result = sut._to_execution_result(
        item=_item(),
        answer=_Answer("select channel, amount, day from payment_transaction order by channel"),
        enabled={},
        tokens_used=10,
        runtime_ms=5,
    )

    assert result.error is None
    assert result.output["columns"] == ["channel", "amount", "day"]
    assert result.output["rows"] == [
        ["IN_STORE", 40505.25, "2026-01-02"],
        ["ONLINE", 12.5, "2026-01-03"],
    ], "Decimal must arrive as float and a date as an ISO string -- the same coercion the gold "
    assert result.output["row_count"] == 2


def test_pushed_values_match_how_the_gold_was_stored(tmp_path: Path) -> None:
    """The comparison is only meaningful if both sides canonicalize to the same thing.

    Gold went through Decimal -> float. `jsonable()` in the grader package would give
    str(Decimal) -- and a string never equals a float, so every money answer would score wrong
    while looking like a real miss. This asserts the type, not just the value.
    """
    corpus = _corpus(tmp_path)
    sut = _sut_over(corpus, tmp_path)

    result = sut._to_execution_result(
        item=_item(),
        answer=_Answer("select sum(amount) as total from payment_transaction"),
        enabled={},
        tokens_used=1,
        runtime_ms=1,
    )

    total = result.output["rows"][0][0]
    assert isinstance(total, float), f"money must be float, got {type(total).__name__}: {total!r}"
    assert total == 40517.75


def test_a_deferral_pushes_no_rows(tmp_path: Path) -> None:
    """A deferral is not an answer; inventing an empty result set for it would let it be graded."""
    corpus = _corpus(tmp_path)
    sut = _sut_over(corpus, tmp_path)

    result = sut._to_execution_result(
        item=_item(),
        answer=_Answer("", deferred=True),
        enabled={},
        tokens_used=1,
        runtime_ms=1,
    )

    assert result.deferred is True
    assert "rows" not in result.output


def test_sql_that_will_not_execute_is_a_visible_error(tmp_path: Path) -> None:
    """mnemiq already executed this SQL to answer, so a failure here means the two disagree. That
    must be loud: silently pushing no rows is what made the first sweep unreadable."""
    corpus = _corpus(tmp_path)
    sut = _sut_over(corpus, tmp_path)

    result = sut._to_execution_result(
        item=_item(),
        answer=_Answer("select * from no_such_table"),
        enabled={},
        tokens_used=1,
        runtime_ms=1,
    )

    assert result.error is not None
    assert "candidate_sql_failed" in result.error
