"""What the report loader must not get wrong.

The loader's job is to carry outputs across a boundary without becoming a second
grader. These pin the two halves of that: the payload it builds says nothing
about correctness, and the agreement table it prints does not mistake the
two-metric distinction for a disagreement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from load_eval_reports import (  # noqa: E402
    Comparison,
    CorpusMismatchError,
    ItemRef,
    ReportFormatError,
    ReportRecord,
    ReportSpec,
    check_corpus,
    ingest_payload,
    load_manifest,
    parse_report,
    read_meta,
    run_config,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


def _record(**over: object) -> ReportRecord:
    base: dict[str, object] = {
        "case_id": "bird-1471",
        "outcome": "correct",
        "sql": "SELECT 1",
        "answer": "one",
        "runtime_ms": 42,
    }
    base.update(over)
    return ReportRecord(**base)  # type: ignore[arg-type]


def test_a_report_line_becomes_a_record() -> None:
    records = parse_report(
        json.dumps({"case_id": "bird-1", "outcome": "correct", "sql": "SELECT 1", "ms": 1234.9})
        + "\n\n"
    )

    assert len(records) == 1
    assert records[0].case_id == "bird-1"
    assert records[0].runtime_ms == 1234


def test_a_line_that_is_not_a_result_record_is_refused() -> None:
    """A training-data JSONL in the same directory must not load as results."""
    with pytest.raises(ReportFormatError, match="not a result record"):
        parse_report(json.dumps({"prompt": "...", "completion": "..."}))


def test_malformed_json_names_its_line() -> None:
    with pytest.raises(ReportFormatError, match="line 2"):
        parse_report('{"case_id":"a","outcome":"correct"}\nnot json\n')


def test_the_payload_carries_outputs_and_never_a_verdict() -> None:
    """Beacon grades server-side; a loader that shipped verdicts would break that."""
    payload = ingest_payload(
        _record(), item_id="0197a0b1-0000-7000-8000-000000000000", engine="duckdb"
    )

    assert payload["output"]["sql"] == "SELECT 1"
    assert payload["output"]["reported_outcome"] == "correct"
    assert "outcome" not in payload
    assert "verdict" not in payload


def test_a_deferral_is_flagged_rather_than_pushed_as_an_empty_answer() -> None:
    """Without the flag an empty SQL grades FAIL, which is the whole of B16."""
    payload = ingest_payload(
        _record(outcome="deferred_wrongly", sql=""), item_id="i", engine="duckdb"
    )

    assert payload["deferred"] is True
    assert payload["error"] is None


def test_an_errored_item_carries_an_error_rather_than_a_deferral() -> None:
    payload = ingest_payload(_record(outcome="error"), item_id="i", engine="duckdb")

    assert payload["deferred"] is False
    assert payload["error"] is not None


def test_per_item_tokens_are_zero_because_the_format_only_has_a_run_total() -> None:
    """Dividing a run total across items would invent per-item cost data."""
    payload = ingest_payload(_record(), item_id="i", engine="duckdb")

    assert payload["tokens_input"] == 0
    assert payload["tokens_output"] == 0


def test_right_data_in_the_wrong_shape_is_agreement_not_disagreement() -> None:
    """`correct_facts` is the second metric, so exact match calling it FAIL agrees."""
    comparison = Comparison()

    comparison.record(_record(outcome="correct_facts"), "FAIL")

    assert comparison.agreed == 1
    assert comparison.disagreed == 0


def test_a_real_disagreement_is_counted_and_marked() -> None:
    comparison = Comparison()

    comparison.record(_record(outcome="correct"), "FAIL")

    assert comparison.disagreed == 1
    assert any("differs" in line for line in comparison.report_lines())


def test_an_unknown_reported_outcome_counts_as_a_disagreement() -> None:
    """A vocabulary beacon does not model must be visible, not silently agreed."""
    comparison = Comparison()

    comparison.record(_record(outcome="partially_correct"), "PASS")

    assert comparison.disagreed == 1


def test_the_run_config_records_which_report_it_came_from() -> None:
    """Config identity is caller-supplied: the file name does not carry it (B19)."""
    config = run_config(
        ReportSpec(file="r.jsonl", model="m", config_label="+guided", layers={"verifier": True}),
        {"tokens": 10, "llm_calls": 2},
    )

    assert config["model_id"] == "m"
    assert config["layers_enabled"] == {"verifier": True}
    assert config["extras"]["config_label"] == "+guided"
    assert config["extras"]["source_report"] == "r.jsonl"
    assert config["extras"]["run_tokens"] == 10


@pytest.fixture
def manifest(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps({"reports": [{"file": "a.jsonl", "model": "m", "config_label": "baseline"}]}),
        encoding="utf-8",
    )
    yield path


def test_a_manifest_entry_becomes_a_spec(manifest: Path) -> None:
    specs = load_manifest(manifest)

    assert [(s.file, s.model, s.config_label) for s in specs] == [("a.jsonl", "m", "baseline")]


def test_a_manifest_entry_without_a_config_label_is_refused(tmp_path: Path) -> None:
    """An unlabelled report cannot be told apart from another run of the same model."""
    path = tmp_path / "m.json"
    path.write_text(json.dumps([{"file": "a.jsonl", "model": "m"}]), encoding="utf-8")

    with pytest.raises(ReportFormatError, match="config_label"):
        load_manifest(path)


def test_a_missing_sidecar_is_not_an_error(tmp_path: Path) -> None:
    report = tmp_path / "r.jsonl"
    report.write_text("", encoding="utf-8")

    assert read_meta(report) == {}


def test_the_sidecar_totals_are_read_when_present(tmp_path: Path) -> None:
    report = tmp_path / "r.jsonl"
    report.write_text("", encoding="utf-8")
    (tmp_path / "r.jsonl.meta.json").write_text(json.dumps({"tokens": 7}), encoding="utf-8")

    assert read_meta(report)["tokens"] == 7


def test_a_report_from_another_corpus_is_refused_not_partly_loaded() -> None:
    """Case ids collide across benchmarks, so a wrong report resolves onto wrong gold.

    Real instance: the KaggleDBQA reports number their cases `bird-0`, `bird-1`,
    ... and 56 of those ids also exist in BIRD mini-dev. Without this check they
    load cleanly and grade GeoNuclearData answers against California school gold.
    """
    records = [
        ReportRecord("bird-5", "correct", "SELECT 1", "", 1, db_id="GeoNuclearData"),
        ReportRecord("bird-11", "wrong", "SELECT 2", "", 1, db_id="GeoNuclearData"),
    ]
    index = {
        "5": ItemRef(item_id="i5", db_id="california_schools"),
        "11": ItemRef(item_id="i11", db_id="financial"),
    }

    with pytest.raises(CorpusMismatchError, match="another corpus"):
        check_corpus(records, index)


def test_the_matching_corpus_passes_the_check() -> None:
    records = [ReportRecord("bird-5", "correct", "SELECT 1", "", 1, db_id="california_schools")]
    index = {"5": ItemRef(item_id="i5", db_id="california_schools")}

    check_corpus(records, index)


def test_a_record_with_no_db_id_cannot_be_checked_and_is_not_refused() -> None:
    """Older reports predate the field; refusing them would be a false positive."""
    records = [ReportRecord("bird-5", "correct", "SELECT 1", "", 1)]
    index = {"5": ItemRef(item_id="i5", db_id="california_schools")}

    check_corpus(records, index)


def test_a_correct_refusal_is_still_a_deferral_on_the_wire() -> None:
    """ACME-style suites have unanswerable questions; refusing one is mnemiq's
    success case, and pushing it as an empty answer would grade it FAIL."""
    payload = ingest_payload(
        _record(outcome="deferred_correctly", sql=""), item_id="i", engine="duckdb"
    )

    assert payload["deferred"] is True


def test_an_unportable_answer_no_longer_flips_the_expectation() -> None:
    """Beacon grades the runner's own rows now, so an unportable-but-right
    answer is expected to PASS; portability is a facet, not a verdict."""
    record = _record(outcome="correct", portable_to_gold_engine=False)
    comparison = Comparison()

    comparison.record(record, "PASS")

    assert comparison.agreed == 1


def test_portability_rides_along_in_the_output() -> None:
    payload = ingest_payload(_record(portable_to_gold_engine=False), item_id="i", engine="duckdb")

    assert payload["output"]["portable_to_gold_engine"] is False
    assert payload["output"]["engine"] == "duckdb"
