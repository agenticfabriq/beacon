"""Record a mnemiq Spider 2.0-lite run in beacon's store.

This IMPORTS verdicts rather than re-driving the questions. The 135 local cases were
already answered by mnemiq's own runner against the benchmark's published result CSVs;
re-asking them through beacon's harness would spend the LLM budget twice for identical
answers. What beacon adds here is the durable record: a Run, per-item Results, and the
suite/items that make a harness-driven arm cheap later.

The outcome mapping is the whole point of the file, so it is explicit:

    correct, correct_facts  -> PASS    (both got the facts; see --strict)
    wrong                   -> FAIL
    deferred_wrongly        -> DEFER   (asked, declined; stays in the denominator)
    error                   -> ERROR   (an outage or a crash, NOT an abstention)

``correct_facts`` counts as PASS by default because Spider 2.0-lite's own grader passes
when every gold column appears among the predicted columns by value -- extra context
columns do not fail there, and calling them failures here would report a stricter number
than the benchmark's own. ``--strict`` restricts PASS to exact matches.

Usage::

    DATABASE_URL=... uv run python scripts/import_spider2_run.py \\
        --results ~/src/fabriq/mnemiq/eval-reports/spider2-results.jsonl \\
        --spider2-dir ~/src/dataset/spider2-lite \\
        --model openai.gpt-5.5 --label single-shot
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from beacon_benchmarks.spider2_lite.ingest_items import (
    DATASET_VERSION,
    SUITE,
    ingest_spider2_tasks,
    load_spider2_tasks,
)
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_storage.db import make_engine, make_session_factory, session_scope
from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.verdicts import VerdictRepo
from uuid_extensions import uuid7

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sqlalchemy.orm import Session

# Names the thing that actually graded: mnemiq's own grader over the benchmark's
# published CSVs. NEVER "result_set_match" -- that name and its version belong to
# beacon's ResultSetMatchGrader, and borrowing them would label imported verdicts
# as beacon's own claim at a version the grader may not even be at. Grader
# identity is provenance, not a genre.
GRADER = "mnemiq.eval.grade.results_match"
GRADER_VERSION = "imported"

# mnemiq CaseResult.outcome -> beacon verdict. correct_facts is decided at call time.
_OUTCOME = {
    "wrong": VerdictOutcome.FAIL,
    "deferred_wrongly": VerdictOutcome.DEFER,
    "deferred_correctly": VerdictOutcome.PASS,
    "error": VerdictOutcome.ERROR,
}


def _sqlalchemy_url(dsn: str) -> str:
    return dsn.replace("postgresql://", "postgresql+psycopg://", 1)


def _seed_user_id(session: Session) -> UUID:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(
            subject="spider2-import-seed",
            email="spider2-import-seed@example.com",
            name="Spider2 Import Seed",
        )
    )
    return user.id


def _verdict(outcome: str, *, strict: bool) -> VerdictOutcome:
    if outcome == "correct":
        return VerdictOutcome.PASS
    if outcome == "correct_facts":
        return VerdictOutcome.FAIL if strict else VerdictOutcome.PASS
    return _OUTCOME.get(outcome, VerdictOutcome.ERROR)


# mnemiq previews rows as a list of dicts; beacon's execution evidence wants the
# columns and a list-of-lists sample, which is what renders the two result tables
# side by side in the compare view.
def _columns_and_sample(
    preview: list[dict[str, Any]] | None, limit: int = 20
) -> tuple[list[str], list[list[Any]]]:
    if not preview:
        return [], []
    columns = list(preview[0].keys())
    return columns, [[row.get(c) for c in columns] for row in preview[:limit]]


def _mismatch(row: dict[str, Any]) -> dict[str, str] | None:
    """Why a WRONG answer differed, in beacon's vocabulary.

    Derived rather than recorded: mnemiq grades against several accepted results and
    keeps the outcome, not the losing comparison. Row count and column arity are exact
    from the preview; anything else is a value difference.
    """
    ours, gold = row.get("engine_row_count"), row.get("gold_row_count")
    if isinstance(ours, int) and isinstance(gold, int) and ours != gold:
        return {"kind": "row_count", "detail": f"candidate returned {ours} rows, gold {gold}"}
    our_cols, _ = _columns_and_sample(row.get("engine_rows"))
    gold_cols, _ = _columns_and_sample(row.get("gold_rows"))
    if our_cols and gold_cols and len(our_cols) != len(gold_cols):
        return {
            "kind": "column_arity",
            "detail": f"candidate has {len(our_cols)} columns, gold {len(gold_cols)}",
        }
    return {"kind": "values", "detail": "same shape, different values"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, help="mnemiq spider2 results .jsonl")
    parser.add_argument("--spider2-dir", required=True, help="extracted spider2-lite dir")
    parser.add_argument("--database-url", help="beacon DB URL; defaults to $DATABASE_URL")
    parser.add_argument("--team", default="mnemiq")
    parser.add_argument("--model", default=os.environ.get("MNEMIQ_LLM_MODEL", "mnemiq"))
    parser.add_argument("--label", default="single-shot", help="config_label for the run")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="count only exact result-set matches as PASS (default: facts, which is what "
        "the benchmark's own grader measures)",
    )
    args = parser.parse_args()

    dsn = args.database_url or os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2

    results_path = Path(args.results).expanduser()
    if not results_path.is_file():
        print(f"no results file: {results_path}", file=sys.stderr)
        return 2
    rows = [json.loads(line) for line in results_path.read_text().splitlines() if line.strip()]
    if not rows:
        print("results file is empty", file=sys.stderr)
        return 2

    meta_path = results_path.with_suffix(results_path.suffix + ".meta.json")
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}

    repo_dir = Path(args.spider2_dir).expanduser() / "repo" / "spider2-lite"
    tasks = load_spider2_tasks(repo_dir)

    engine = make_engine(_sqlalchemy_url(dsn))
    try:
        with session_scope(make_session_factory(engine)) as session:
            user_id = _seed_user_id(session)
            team = TeamRepo(session).get_by_name(args.team)
            if team is None:
                team = TeamRepo(session).create(name=args.team)

            ingested = ingest_spider2_tasks(
                session, team_id=team.id, tasks=tasks, created_by=user_id
            )

            suite_repo = SuiteRepo(session)
            suite_row = suite_repo.get_by_team_and_name(team.id, SUITE)
            if suite_row is None:
                suite_row = suite_repo.create(
                    team_id=team.id,
                    name=SUITE,
                    description="Spider 2.0-lite, local SQLite slice (135 cases).",
                    method="manual",
                    suite_metadata={"source": "spider2-lite-local"},
                    created_by=user_id,
                )
            item_rows = EvalItemRepo(session).list_active(suite=SUITE, team_id=team.id)
            suite_repo.add_items(suite_id=suite_row.id, item_ids=[r.item_id for r in item_rows])

            # results.item_id carries the eval_item UUID (what grading and the
            # matrix join on), never the benchmark's native case id.
            item_by_case = {
                str((r.item_metadata or {}).get("instance_id")): str(r.item_id)
                for r in item_rows
            }
            accepted_counts = {
                str((r.item_metadata or {}).get("instance_id")): (
                    r.item_metadata or {}
                ).get("accepted_result_count")
                for r in item_rows
            }

            from beacon_runner.sut.mnemiq import register_mnemiq_solution  # noqa: PLC0415

            solution = _solution_id(session, team.id, user_id, register_mnemiq_solution)

            counts: dict[str, int] = {}
            run = RunRepo(session).create(
                team_id=team.id,
                solution_id=solution,
                suite_id=suite_row.id,
                suite=SUITE,
                dataset_version=DATASET_VERSION,
                mode=HarnessMode.EVAL,
                pass_idx=0,
                # Recorded so the number is never read without its conditions.
                config={
                    "executor": "sqlite-native",
                    "candidates": 1,
                    "external_knowledge": True,
                    "graded_by": "mnemiq.eval.grade.results_match",
                    "pass_semantics": "strict" if args.strict else "facts",
                    # Basename only: a full path leaks the machine home directory,
                    # and this repo is public-track (repo-guard's rule).
                    "imported_from": results_path.name,
                    "driver": "mnemiq scripts/run_spider2.py",
                },
                model_id=args.model,
                config_label=args.label,
                created_by=user_id,
                parent_sweep_id=uuid7(),  # makes each import a distinct run
            )
            RunRepo(session).mark_running(run.id)

            result_repo = ResultRepo(session)
            verdict_repo = VerdictRepo(session)
            for row in rows:
                case_id = str(row.get("case_id", ""))
                item_uuid = item_by_case.get(case_id)
                if item_uuid is None:
                    raise SystemExit(
                        f"case_id {case_id!r} is not among the suite's items; "
                        "load the benchmark before importing a run against it"
                    )
                outcome = str(row.get("outcome", "error"))
                counts[outcome] = counts.get(outcome, 0) + 1
                verdict = _verdict(outcome, strict=args.strict)
                output: dict[str, Any] = {
                    "sql": row.get("sql", ""),
                    "answer": row.get("answer", ""),
                    "db_id": row.get("db_id"),
                    "engine_row_count": row.get("engine_row_count"),
                    "gold_row_count": row.get("gold_row_count"),
                    "mnemiq_outcome": outcome,
                }
                # The runner's claim that this SQL already ran on the gold engine
                # (for the local slice, sqlite-native means it always did). The
                # matrix's EX* reads it from output; absent means "never claimed".
                if (portable := row.get("portable_to_gold_engine")) is not None:
                    output["portable_to_gold_engine"] = portable
                result_row = result_repo.create(
                    team_id=team.id,
                    run_id=run.id,
                    item_id=item_uuid,
                    attempt_idx=0,
                    output=output,
                    output_kind="json",
                    tokens_input=0,
                    tokens_output=0,
                    runtime_ms=int(row.get("ms") or 0),
                    status=(
                        ResultStatus.ERROR if outcome == "error" else ResultStatus.COMPLETED
                    ),
                    outcome=verdict,
                    error=row.get("answer") if outcome == "error" else None,
                )

                # Only executed cases get verdicts. A deferral or an outage produced no
                # result set to compare, and a got_facts verdict of "false" would report
                # a wrong answer where there was no answer at all. Corollary: never
                # compute a rate from the verdicts table alone -- the denominator
                # (all graded results, deferrals included) lives in results, which
                # is how the matrix query reads it.
                if outcome not in {"correct", "correct_facts", "wrong"}:
                    continue
                exact = outcome == "correct"
                facts = outcome in {"correct", "correct_facts"}
                our_cols, our_sample = _columns_and_sample(row.get("engine_rows"))
                gold_cols, gold_sample = _columns_and_sample(row.get("gold_rows"))
                raw: dict[str, object] = {
                    "candidate_sql": row.get("sql", ""),
                    "gold_sql": row.get("gold_sql", ""),
                    "candidate_row_count": row.get("engine_row_count"),
                    "gold_row_count": row.get("gold_row_count"),
                    "candidate_columns": our_cols,
                    "gold_columns": gold_cols,
                    "candidate_sample": our_sample,
                    "gold_sample": gold_sample,
                    # Gold is a set of accepted results here, and the case passes against
                    # any of them; the preview shows the first.
                    "accepted_result_count": accepted_counts.get(case_id),
                }
                mismatch = None if exact else _mismatch(row)
                if mismatch is not None:
                    raw["mismatch"] = mismatch
                if (portable := row.get("portable_to_gold_engine")) is not None:
                    raw["portable_to_gold_engine"] = portable
                verdict_repo.create(
                    team_id=team.id,
                    result_id=result_row.id,
                    grader=GRADER,
                    grader_version=GRADER_VERSION,
                    metric="exact_match",
                    criterion="correctness",
                    bool_value=exact,
                    value=1.0 if exact else 0.0,
                    justification=(
                        "Candidate result set matches an accepted gold result."
                        if exact
                        else f"Mismatch ({(mismatch or {}).get('kind')})."
                    ),
                    raw_output=raw,
                )
                verdict_repo.create(
                    team_id=team.id,
                    result_id=result_row.id,
                    grader=GRADER,
                    grader_version=GRADER_VERSION,
                    metric="got_facts",
                    criterion="correctness",
                    bool_value=facts,
                    value=1.0 if facts else 0.0,
                    justification=(
                        "Gold's data is present in the candidate (shape-tolerant)."
                        if facts
                        else "Gold's data is not present in the candidate, in any projection."
                    ),
                    raw_output={"exact_match": exact},
                )
            RunRepo(session).mark_completed(run.id)

            total = len(rows)
            passed = sum(
                1 for r in rows if _verdict(str(r.get("outcome")), strict=args.strict)
                == VerdictOutcome.PASS
            )
            print(f"suite     {SUITE} ({DATASET_VERSION})")
            print(f"items     {ingested.inserted} inserted, {ingested.skipped} already present")
            print(f"run       {run.id}  [{args.label}, {'strict' if args.strict else 'facts'}]")
            print(f"results   {total} imported")
            for name in sorted(counts):
                print(f"  {name:20s} {counts[name]}")
            print(f"pass rate {passed / total:.1%}  ({passed}/{total})")
            if meta:
                print(f"llm calls {meta.get('llm_calls', 0)}   tokens {meta.get('tokens', 0)}")
    finally:
        engine.dispose()
    return 0


def _solution_id(
    session: Session,
    team_id: UUID,
    user_id: UUID,
    register: Callable[..., Any],
) -> UUID:
    """The Solution row for the system that was measured: mnemiq's engine.

    ``MnemiqInProcessSUT.SOLUTION_ID`` is ``"mnemiq"`` -- it names the engine, not the
    BIRD harness, and the layers it declares (enrichment, grounding, verifier,
    self_consistency, mode_routing) are properties of that engine, equally true of this
    run. So it is the right row even though this run was driven by mnemiq's own Spider
    runner, which the Run config records.

    It is constructed only to be asked its identity; the constructor stores paths and
    opens nothing, and no method that would use them is called here. The dataset
    arguments are therefore named for what they are: unused.
    """
    from beacon_runner.registry import SutRegistry  # noqa: PLC0415
    from beacon_runner.sut.mnemiq import MnemiqInProcessSUT  # noqa: PLC0415

    sut = MnemiqInProcessSUT(
        owner_team_id=team_id,
        minidev_dir="<unused: verdicts imported, not produced here>",
        bird_dsn="<unused>",
        enrich_cache_dir="<unused>",
        candidates=1,
    )
    solution = register(
        session, team_id=team_id, created_by=user_id, sut=sut, registry=SutRegistry()
    )
    solution_id: UUID = solution.id
    return solution_id


if __name__ == "__main__":
    sys.exit(main())
