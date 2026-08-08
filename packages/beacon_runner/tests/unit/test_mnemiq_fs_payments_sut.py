"""The grounded arm must be unable to silently equal baseline.

Every assertion here corresponds to a way that actually happened while building the corpus, not to
a hypothetical. A gate that has never been shown to fire is decoration.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from beacon_runner.sut.mnemiq.fs_payments import (
    CertifiedRecordsNotGrounded,
    MnemiqFsPaymentsSUT,
)


class _Snapshot:
    def __init__(self, definitions=(), metrics=(), dimensions=()):
        self.definitions = list(definitions)
        self.metrics = list(metrics)
        self.dimensions = list(dimensions)


def _sut(tmp_path: Path, *, expect: int = 38) -> MnemiqFsPaymentsSUT:
    records = tmp_path / "certified_records.json"
    records.write_text(json.dumps({"records": []}))
    return MnemiqFsPaymentsSUT(
        owner_team_id=uuid4(),
        database_path=str(tmp_path / "corpus.duckdb"),
        records_url=f"file://{records}",
        enrich_cache_dir=str(tmp_path / "cache"),
        expect_records=expect,
    )


def test_a_short_fetch_is_refused(tmp_path):
    """The fail-soft 401. `fetch_certified_records` returns [] on any transport failure so a Verity
    outage cannot brick mnemiq — correct for the product, fatal for an experiment, because both
    arms then produce an identical prompt and the run reports no effect."""
    sut = _sut(tmp_path)

    with pytest.raises(CertifiedRecordsNotGrounded, match="fetched 0 certified records"):
        sut._assert_grounded(_Snapshot(), [], before=0)


def test_an_empty_incremental_delta_is_refused(tmp_path):
    """The watermark. A successful pull stamps one; the next sends `?since=` and receives nothing.
    An eval whose second run grounds on less than its first, with nothing saying so."""
    sut = _sut(tmp_path, expect=38)

    with pytest.raises(CertifiedRecordsNotGrounded, match="expected 38"):
        sut._assert_grounded(_Snapshot(), [object()], before=0)


def test_records_that_never_reach_the_packet_are_refused(tmp_path):
    """The third way, and the one a snapshot-level check misses: certified metrics and dimensions
    sat in `snapshot.metrics` read by nothing for as long as they existed."""
    sut = _sut(tmp_path, expect=1)
    # Applied to the snapshot -- `before` was 0 and the snapshot now holds one -- but bound to a
    # table no question retrieves, so nothing is selected into the packet.
    elsewhere = _Definition(term="unrelated", bound_objects=["some_other_table"])

    with pytest.raises(CertifiedRecordsNotGrounded, match="retrieval packet"):
        sut._assert_grounded(_Snapshot(definitions=[elsewhere]), [object()], before=0)


def test_a_grounded_arm_that_grounded_passes(tmp_path):
    """Non-vacuity: the gate must not refuse everything. A definition bound to the probe table
    rides with it into the packet, which is what grounding looks like when it worked."""
    sut = _sut(tmp_path, expect=1)
    bound = _Definition(term="revenue", bound_objects=["payment_transaction"])

    sut._assert_grounded(_Snapshot(definitions=[bound]), [object()], before=0)


def test_run_config_records_the_snapshot_it_read(tmp_path):
    """Two checkouts carrying the same basename at different states is the same trap as two
    databases with the same name, so the digest is of the bytes actually read."""
    sut = _sut(tmp_path)

    config = sut.run_config()

    assert config["records_sha256"] is not None, "a local snapshot must carry its digest"
    assert config["expect_records"] == 38
    assert config["verity_records_url"].startswith("file://")


def test_a_remote_records_url_admits_it_has_no_digest(tmp_path):
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


class _Definition:
    """The shape `select_definitions` reads: a term, a public flag, and its bindings."""

    def __init__(self, *, term: str, bound_objects: list[str]):
        self.id = f"def:{term}"
        self.term = term
        self.definition = "..."
        self.public = False
        self.bound_objects = bound_objects
