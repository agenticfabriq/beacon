"""Load JSONL eval reports produced off-platform into beacon, and re-grade them.

Beacon is a tracker: a runner executes a suite on its own hardware and pushes
the outputs back. This script is the bridge for runners that write their results
to a JSONL file rather than calling the API directly.

**Beacon re-grades everything.** The report's own ``outcome`` is carried into
``output.reported_outcome`` for comparison only; the verdict beacon stores is
computed here, from the raw SQL, against the suite's benchmark database. That
makes every load a conformance check between two independent graders, and the
disagreement table this prints is the point of it.

Expected input: one JSON object per line, with at least::

    {"case_id": "bird-1471",        # maps to an eval item's question_hash tail
     "outcome": "correct",          # the runner's own verdict; see OUTCOME_MEANING
     "sql": "SELECT ...",           # the candidate answer, empty when deferred
     "answer": "...",               # optional prose answer
     "db_id": "california_schools", # which database it was asked against
     "ms": 6875.6}                  # optional wall-clock for this item

``case_id`` is **not corpus-qualified**: two different benchmarks can both number
their cases ``bird-0``, ``bird-1``, and so on. Loading one corpus's report against
another's gold would grade real answers against unrelated questions and look
entirely plausible, so every mapped record's ``db_id`` is checked against the
eval item's, and a report that mismatches is refused rather than partly loaded.

An optional ``<report>.meta.json`` sidecar carries run-level totals::

    {"tokens": 1653599, "llm_calls": 1114, "excluded": ["bird-1088", ...]}

Tokens are a run-level total in this format, so per-item token counts are left
at zero rather than fabricated by division.

Usage::

    export BEACON_API_KEY=... DATABASE_URL=...
    uv run python scripts/load_eval_reports.py \
        --solution <uuid> --suite <uuid> \
        --manifest reports.json --reports-dir path/to/reports
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

# What each runner-reported outcome means, and what beacon's exact-match grader
# should therefore say. ``correct_facts`` is right data in a shape exact match
# does not accept, so FAIL is the agreeing answer, not a disagreement. Both
# deferral outcomes are DEFER here: whether the refusal was the right call is
# the runner's distinction, not a different fact about what happened.
OUTCOME_MEANING: dict[str, str] = {
    "correct": "PASS",
    "correct_facts": "FAIL",
    "wrong": "FAIL",
    "deferred_wrongly": "DEFER",
    "deferred_correctly": "DEFER",
    "error": "ERROR",
}
DEFERRED_OUTCOMES = frozenset({"deferred_wrongly", "deferred_correctly"})
ERRORED_OUTCOME = "error"


class ReportFormatError(Exception):
    """Raised when a report line cannot be read as a result record."""


@dataclass(frozen=True)
class ReportRecord:
    """One item's result as the runner recorded it."""

    case_id: str
    outcome: str
    sql: str
    answer: str
    runtime_ms: int
    db_id: str = ""
    # Whether the SQL also runs on the gold's engine. None on single-engine
    # reports and reports written before the runner recorded it.
    portable_to_gold_engine: bool | None = None

    @property
    def deferred(self) -> bool:
        """Whether the runner declined to answer this item."""
        return self.outcome in DEFERRED_OUTCOMES

    @property
    def errored(self) -> bool:
        """Whether the attempt could not run."""
        return self.outcome == ERRORED_OUTCOME


@dataclass(frozen=True)
class ReportSpec:
    """A report file plus the configuration it was produced under.

    The file name is not a config identity: nothing in the report says which
    model or which knobs produced it, so the caller must say (cf. B19).
    """

    file: str
    model: str
    config_label: str
    layers: dict[str, bool] = field(default_factory=dict)
    prompt_version: str = "v0"


@dataclass
class Comparison:
    """Agreement between the runner's verdicts and beacon's."""

    agreed: int = 0
    disagreed: int = 0
    skipped: int = 0
    cross: dict[tuple[str, str], int] = field(default_factory=dict)

    def record(self, record: ReportRecord, computed: str) -> None:
        """Count one item against what beacon computed for it."""
        reported = record.outcome
        self.cross[reported, computed] = self.cross.get((reported, computed), 0) + 1
        if self.expected(record) == computed:
            self.agreed += 1
        else:
            self.disagreed += 1

    @staticmethod
    def expected(record: ReportRecord) -> str | None:
        """What beacon should say about this record.

        Beacon executes the candidate on the gold's own engine, so an answer the
        runner already knows is unportable will fail here even when the runner
        scored it correct on its own executor. That is agreement about the
        facts, not a grader disagreement, and counting it as one buries the
        table in a known cause.
        """
        if record.portable_to_gold_engine is False and record.outcome in (
            "correct",
            "correct_facts",
        ):
            return "FAIL"
        return OUTCOME_MEANING.get(record.outcome)

    def report_lines(self) -> list[str]:
        """Render the cross-tabulation, disagreements marked."""
        lines = [f"agreed={self.agreed} disagreed={self.disagreed} skipped={self.skipped}"]
        for (reported, computed), count in sorted(self.cross.items(), key=lambda kv: -kv[1]):
            differs = "  <-- differs" if OUTCOME_MEANING.get(reported) != computed else ""
            lines.append(f"  {reported:<20} -> {computed:<6} {count:>5}{differs}")
        return lines


def parse_report(content: str) -> list[ReportRecord]:
    """Parse a report's JSONL body, skipping blank lines."""
    records: list[ReportRecord] = []
    for number, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw: Any = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ReportFormatError(f"line {number} is not JSON: {exc}") from exc
        if not isinstance(raw, dict) or "case_id" not in raw or "outcome" not in raw:
            raise ReportFormatError(f"line {number} has no case_id/outcome; not a result record")
        records.append(
            ReportRecord(
                case_id=str(raw["case_id"]),
                outcome=str(raw["outcome"]),
                sql=str(raw.get("sql") or ""),
                answer=str(raw.get("answer") or ""),
                runtime_ms=int(float(raw.get("ms") or 0)),
                db_id=str(raw.get("db_id") or ""),
                portable_to_gold_engine=(
                    bool(raw["portable_to_gold_engine"])
                    if isinstance(raw.get("portable_to_gold_engine"), bool)
                    else None
                ),
            )
        )
    return records


def read_meta(report_path: Path) -> dict[str, Any]:
    """Return the report's sidecar totals, or an empty mapping when absent."""
    sidecar = report_path.with_suffix(report_path.suffix + ".meta.json")
    if not sidecar.exists():
        return {}
    loaded: Any = json.loads(sidecar.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def ingest_payload(record: ReportRecord, *, item_id: str) -> dict[str, Any]:
    """Build the ingestion body for one record.

    Carries outputs, never verdicts: ``reported_outcome`` rides along as data so
    the two graders can be compared, and beacon still decides the outcome.
    """
    return {
        "item_id": item_id,
        "attempt_idx": 0,
        "output": {
            "sql": record.sql,
            "answer": record.answer,
            "reported_outcome": record.outcome,
            # Ride-along evidence: which answers the runner already knows are
            # unportable, so a cross-grader diff can separate dialect from wrongness.
            "reported_portable": record.portable_to_gold_engine,
        },
        "output_kind": "sql",
        # A run-level token total cannot be divided across items honestly.
        "tokens_input": 0,
        "tokens_output": 0,
        "runtime_ms": record.runtime_ms,
        "deferred": record.deferred,
        "error": "runner reported an error for this item" if record.errored else None,
    }


def run_config(spec: ReportSpec, meta: Mapping[str, Any]) -> dict[str, Any]:
    """Build the run's config, with the run-level totals under extras."""
    return {
        "model_id": spec.model,
        "prompt_version": spec.prompt_version,
        "layers_enabled": dict(spec.layers),
        "secret_refs": {},
        "extras": {
            "config_label": spec.config_label,
            "source_report": spec.file,
            "run_tokens": meta.get("tokens"),
            "llm_calls": meta.get("llm_calls"),
        },
    }


def load_manifest(path: Path) -> list[ReportSpec]:
    """Read a manifest of report files and the configuration each was run under."""
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    entries: Any = loaded.get("reports") if isinstance(loaded, dict) else loaded
    if not isinstance(entries, list):
        raise ReportFormatError("manifest must be a list, or an object with a 'reports' list")
    specs: list[ReportSpec] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReportFormatError(f"manifest entry is not an object: {entry!r}")
        missing = {"file", "model", "config_label"} - set(entry)
        if missing:
            raise ReportFormatError(f"manifest entry missing {sorted(missing)}: {entry!r}")
        raw_layers: Any = entry.get("layers") or {}
        specs.append(
            ReportSpec(
                file=str(entry["file"]),
                model=str(entry["model"]),
                config_label=str(entry["config_label"]),
                layers={str(k): bool(v) for k, v in dict(raw_layers).items()},
                prompt_version=str(entry.get("prompt_version", "v0")),
            )
        )
    return specs


@dataclass(frozen=True)
class ItemRef:
    """An eval item, and the database its question is asked against."""

    item_id: str
    db_id: str


class CorpusMismatchError(Exception):
    """Raised when a report's cases do not belong to the suite being loaded."""


def check_corpus(records: Sequence[ReportRecord], index: Mapping[str, ItemRef]) -> None:
    """Refuse a report whose cases name a different corpus.

    Case ids collide across benchmarks, so a wrong report does not fail to
    resolve -- it resolves onto the wrong questions. The ``db_id`` each record
    carries is what tells them apart.
    """
    mismatched: list[str] = []
    matched = 0
    for record in records:
        ref = index.get(case_key(record.case_id))
        if ref is None or not record.db_id or not ref.db_id:
            continue
        if record.db_id == ref.db_id:
            matched += 1
        else:
            mismatched.append(
                f"{record.case_id}: report says {record.db_id!r}, item says {ref.db_id!r}"
            )
    if mismatched:
        raise CorpusMismatchError(
            f"{len(mismatched)} of {len(mismatched) + matched} resolvable cases name a different "
            f"database than the eval item they map to; this report is for another corpus. "
            f"First three: {'; '.join(mismatched[:3])}"
        )


def case_key(case_id: str) -> str:
    """Return the numeric tail a case id and a question hash share."""
    return case_id.rsplit("-", maxsplit=1)[-1]


def item_index(database_url: str, *, suite: str) -> dict[str, ItemRef]:
    """Map a report ``case_id`` onto the eval item it names.

    The convention is ``<prefix>-<n>`` in the report against a question hash
    ending in ``:<n>`` in beacon, which is how the BIRD adapter writes them.
    """
    engine = create_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT question_hash, item_id, item_input ->> 'db_id' FROM eval_items "
                "WHERE suite = :suite AND valid_to IS NULL"
            ),
            {"suite": suite},
        ).all()
    index: dict[str, ItemRef] = {}
    for question_hash, item_id, db_id in rows:
        tail = str(question_hash).rsplit(":", maxsplit=1)[-1]
        if tail:
            index[tail] = ItemRef(item_id=str(item_id), db_id=str(db_id or ""))
    return index


class BeaconClient:
    """The three ingestion calls this script needs."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(headers={"X-API-Key": api_key}, timeout=180.0)

    def create_run(self, *, solution_id: str, suite_id: str, config: Mapping[str, Any]) -> str:
        """Register a run and return its id."""
        response = self.http.post(
            f"{self.base}/v1/suites/{suite_id}/runs",
            json={
                "solution_id": solution_id,
                "mode": "EVAL",
                "config": dict(config),
            },
        )
        response.raise_for_status()
        return str(response.json()["run_id"])

    def push(self, run_id: str, payload: Mapping[str, Any]) -> str:
        """Push one result and return the outcome beacon composed for it."""
        response = self.http.post(
            f"{self.base}/v1/runs/{run_id}/results",
            json=dict(payload),
        )
        response.raise_for_status()
        return str(response.json()["outcome"])

    def complete(self, run_id: str) -> dict[str, Any]:
        """Close the run."""
        response = self.http.post(f"{self.base}/v1/runs/{run_id}/complete")
        response.raise_for_status()
        result: Any = response.json()
        return dict(result)

    def close(self) -> None:
        """Release the HTTP connection pool."""
        self.http.close()


def load_one(
    client: BeaconClient,
    spec: ReportSpec,
    *,
    path: Path,
    index: Mapping[str, ItemRef],
    solution_id: str,
    suite_id: str,
    limit: int = 0,
) -> tuple[str, Comparison]:
    """Load one report into a fresh run and return the run id and comparison."""
    records = parse_report(path.read_text(encoding="utf-8"))
    if limit:
        records = records[:limit]
    check_corpus(records, index)
    meta = read_meta(path)
    run_id = client.create_run(
        solution_id=solution_id, suite_id=suite_id, config=run_config(spec, meta)
    )
    comparison = Comparison()
    for record in records:
        ref = index.get(case_key(record.case_id))
        if ref is None:
            comparison.skipped += 1
            continue
        computed = client.push(run_id, ingest_payload(record, item_id=ref.item_id))
        comparison.record(record, computed)
    client.complete(run_id)
    return run_id, comparison


def _specs(args: argparse.Namespace) -> Iterator[tuple[ReportSpec, Path]]:
    directory = Path(args.reports_dir) if args.reports_dir else Path()
    if args.manifest:
        for spec in load_manifest(Path(args.manifest)):
            yield spec, directory / spec.file
        return
    report = Path(args.report)
    yield (
        ReportSpec(
            file=report.name,
            model=args.model,
            config_label=args.config_label,
            layers=json.loads(args.layers),
        ),
        report,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solution", required=True, help="registered solution UUID")
    parser.add_argument("--suite", required=True, help="suite UUID the runs belong to")
    parser.add_argument("--suite-name", default="bird_minidev_v2", help="suite name of the items")
    parser.add_argument("--manifest", help="JSON manifest of reports and their configurations")
    parser.add_argument("--reports-dir", help="directory the manifest's files are relative to")
    parser.add_argument("--report", help="a single report file, instead of a manifest")
    parser.add_argument("--model", default="", help="model id, with --report")
    parser.add_argument("--config-label", default="", help="config label, with --report")
    parser.add_argument("--layers", default="{}", help="JSON layer -> bool, with --report")
    parser.add_argument("--limit", type=int, default=0, help="load only the first N records")
    parser.add_argument("--api-base", default=os.environ.get("BEACON_API_BASE", ""))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Load every report named on the command line, printing agreement per report."""
    args = _parser().parse_args(argv)
    if not args.manifest and not args.report:
        print("give --manifest or --report", file=sys.stderr)
        return 2
    if args.report and not (args.model and args.config_label):
        print("--report needs --model and --config-label", file=sys.stderr)
        return 2

    api_base = args.api_base or "http://127.0.0.1:8000"
    try:
        api_key = os.environ["BEACON_API_KEY"]
        database_url = os.environ["DATABASE_URL"]
    except KeyError as exc:
        print(f"{exc.args[0]} is not set", file=sys.stderr)
        return 2

    index = item_index(database_url, suite=args.suite_name)
    print(f"{len(index)} eval items in {args.suite_name}")
    client = BeaconClient(api_base, api_key)
    totals = Comparison()
    try:
        for spec, path in _specs(args):
            if not path.exists():
                print(f"!! missing report {path}", file=sys.stderr)
                continue
            try:
                run_id, comparison = load_one(
                    client,
                    spec,
                    path=path,
                    index=index,
                    solution_id=args.solution,
                    suite_id=args.suite,
                    limit=args.limit,
                )
            except CorpusMismatchError as exc:
                print(f"\n{spec.file}: REFUSED -- {exc}", file=sys.stderr)
                continue
            print(f"\n{spec.file}  [{spec.model} / {spec.config_label}]  run {run_id}")
            for line in comparison.report_lines():
                print(f"  {line}")
            totals.agreed += comparison.agreed
            totals.disagreed += comparison.disagreed
            totals.skipped += comparison.skipped
            for key, count in comparison.cross.items():
                totals.cross[key] = totals.cross.get(key, 0) + count
    finally:
        client.close()

    print("\n=== all reports ===")
    for line in totals.report_lines():
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
