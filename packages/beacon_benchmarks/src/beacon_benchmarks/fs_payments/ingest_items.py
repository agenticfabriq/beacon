"""Ingest the fs payments suite: 29 questions in three bands, gold measured by execution.

The corpus exists to answer one question — does Verity's certified semantic layer change mnemiq's
answers — so the suite is built so that a null result cannot be manufactured by accident:

* **meaning** (14) — the certified record is the only way to be right. Each was authored with the
  reading a schema-only agent would plausibly reach, and the authoring refused any whose naive
  answer already equalled gold, or came within 1% of it.
* **schema** (10) — the CONTROL. The schema answers these unaided; grounding should barely move
  them, and a large move means the layer is improving prompts in general rather than supplying
  meaning. Reporting only an aggregate would hide that in both directions.
* **unanswerable** (5) — nothing in the corpus answers them. Deferring is correct, and a layer that
  makes a system *more* confident about these has made it worse.

Gold is **measured, not written**: every ``gold_sql`` is executed against the corpus at ingest and
the rows recorded as they came back.

Three things this gets right because beacon paid for them:

1. **Typed rows with an ordered ``columns`` array.** DuckDB returns natively-typed values, so no
   CSV-style coercion is needed — but the column order still has to be stamped, because JSONB
   canonicalizes object keys at rest and column order is part of exact match. A regrade refuses
   evidence without one; refusing beats mangling.
2. **Grading annotations ride as item data, when there are any.** ``metadata.tolerance`` carries
   *curated* opinion and outranks the grader's ORDER BY heuristic — so this suite writes none,
   because every opinion it could state is one the heuristic already reaches. A derivation stored
   with curation authority launders its own provenance.
3. **``answerable`` is outcome semantics, not grading.** A correct deferral is the runner's
   statement (``deferred_correctly``); it carries no verdict and must never be derived from the
   absence of a passing one. The flag rides in item input so the runner can say it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from beacon_runner.transport import transport_value

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

SUITE = "fs_payments_v1"
DATASET_VERSION = "fs-payments-v1"

# beacon-main's call, and the reasoning is on the record: gold is measured on the same DuckDB
# engine family mnemiq executes on, so the cross-engine float-noise excuse that justifies a
# tolerant headline does not exist here. The traps are MEANING traps, and a meaning fix that only
# shows up under a tolerant reading is a weak claim. `got_facts` rides alongside for free.
#
# Declared here for the registration step to stamp on `suites.metadata`, which is where the
# derivation and the regrade read it. It is deliberately NOT copied onto items: a second home for
# a declaration drives nothing and can only drift from the one that does.
HEADLINE_METRIC = "exact_match"


@dataclass(frozen=True)
class FsPaymentsTask:
    case_id: str
    question: str
    gold_sql: str | None
    answerable: bool
    band: str
    trap: str | None


@dataclass(frozen=True)
class IngestResult:
    inserted: int
    skipped: int
    refreshed: int


def load_tasks(gold_path: Path) -> list[FsPaymentsTask]:
    cases = json.loads(gold_path.read_text())
    tasks = []
    for case in cases:
        tags = case.get("tags") or []
        tasks.append(
            FsPaymentsTask(
                case_id=case["id"],
                question=case["question"],
                gold_sql=case.get("gold_sql"),
                answerable=bool(case.get("answerable", True)),
                band=tags[0] if tags else "unknown",
                trap=tags[1] if len(tags) > 1 else None,
            )
        )
    return tasks


def _json_safe(value: object) -> object:
    """The shared transport rule, by import rather than by copy.

    Measured against the real corpus: 10 of the 24 gold results failed `json.dumps` outright --
    DuckDB returns `Decimal` for the money columns and `datetime` for the date ones, and neither
    survives JSONB. The rule's one home is ``beacon_runner.transport`` (Decimal -> float,
    temporal -> ISO, nothing else coerced); the agreement test holds every consumer to it.
    """
    return transport_value(value)


def execute_gold(task: FsPaymentsTask, con: Any) -> dict[str, object] | None:
    """The gold result, as the database returns it.

    ``columns`` is stamped in the order the query declared. That order is part of exact match and
    survives nothing else: dict-shaped rows carry theirs only while in flight.
    """
    if task.gold_sql is None:
        return None
    cursor = con.execute(task.gold_sql)
    columns = [description[0] for description in cursor.description]
    rows = [[_json_safe(value) for value in row] for row in cursor.fetchall()]
    return {"columns": columns, "rows": rows}


def ingest_fs_payments_tasks(
    *,
    gold_path: Path,
    database_path: Path,
    items_repo: Any,
    team_id: UUID,
    created_by: UUID,
) -> IngestResult:
    import duckdb
    from beacon_storage.models.eval_items import EvalItemTier  # noqa: PLC0415

    con = duckdb.connect(str(database_path), read_only=True)
    inserted = skipped = refreshed = 0

    corpus_digest = hashlib.sha256(database_path.read_bytes()).hexdigest()

    for task in load_tasks(gold_path):
        question_hash = f"{SUITE}:local:{task.case_id}"
        item_input = {
            "db_id": SUITE,
            "question": task.question,
            # Outcome semantics, not grading. The runner reads this to say `deferred_correctly`
            # rather than anything inferring a deferral from a missing verdict.
            "answerable": task.answerable,
        }

        gold_table = execute_gold(task, con)
        gold_answer: dict[str, object] = {
            # One accepted result: this corpus has a single certified reading per question, which
            # is the point of it. The shape stays plural so the grader reads one key everywhere.
            "accepted_results": [gold_table] if gold_table else [],
            "condition_cols": [],
            "sql": task.gold_sql,
        }

        item_metadata: dict[str, object] = {
            "source": "fs_payments_local",
            "case_id": task.case_id,
            "band": task.band,
            "corpus_sha256": corpus_digest,
        }
        if task.trap:
            item_metadata["trap"] = task.trap
        # No `tolerance` key. `metadata.tolerance` carries CURATED opinion, which outranks the
        # grader's ORDER BY heuristic -- and every opinion this suite could state is one the
        # heuristic already reaches on its own. Writing a derivation into a field that means
        # "a human decided this" would launder its provenance for no behavioural gain.

        item, created = items_repo.upsert_by_question_hash(
            # The gold came from executing the query against the corpus, not from a published file.
            tier=EvalItemTier.EXECUTION_CONFIRMED,
            suite=SUITE,
            team_id=team_id,
            dataset_version=DATASET_VERSION,
            question_hash=question_hash,
            item_input=item_input,
            gold_answer=gold_answer,
            item_metadata=item_metadata,
            created_by=created_by,
        )
        if created:
            inserted += 1
            continue
        if (
            item.item_input == item_input
            and item.gold_answer == gold_answer
            and item.item_metadata == item_metadata
            and item.dataset_version == DATASET_VERSION
        ):
            skipped += 1
            continue
        now = datetime.now(UTC)
        items_repo.set_valid_to(item_id=item.item_id, valid_from=item.valid_from, valid_to=now)
        items_repo.insert_new_version(
            item_id=item.item_id,
            valid_from=now,
            tier=EvalItemTier.EXECUTION_CONFIRMED,
            suite=SUITE,
            team_id=team_id,
            solution_id=item.solution_id,
            dataset_version=DATASET_VERSION,
            item_input=item_input,
            gold_answer=gold_answer,
            item_metadata=item_metadata,
            question_hash=question_hash,
            created_by=created_by,
        )
        refreshed += 1

    con.close()
    return IngestResult(inserted=inserted, skipped=skipped, refreshed=refreshed)
