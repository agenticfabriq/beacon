"""Load Spider 2.0-lite local tasks and ingest them into beacon eval_items.

Two things differ from BIRD Mini-Dev and shape everything here:

* **Gold is a set of result tables, not a query.** Only a minority of local cases
  publish gold SQL; the benchmark's own grader compares result CSVs, and nearly every
  case publishes SEVERAL acceptable results. So ``gold_answer`` carries the accepted
  result tables, and a case passes against *any* of them. Storing one SQL string would
  assert a single right query the benchmark does not claim exists.
* **The slice is deliberately partial.** Only ``local*`` instances are ingested: the
  BigQuery and Snowflake instances need cloud credentials and adapters, so they are
  absent rather than failing.

The benchmark also publishes per-instance grading annotations
(``spider2lite_eval.jsonl``): ``ignore_order`` and ``condition_cols``. They ride
along as item data -- a row-order tolerance and a per-accepted-result list of the
gold columns its evaluator scores -- so one grader can honour them per item
instead of Spider growing a grader of its own.

Tier is EXECUTION_CONFIRMED, not HUMAN_VERIFIED: the accepted answers were produced by
executing the benchmark's own queries, which is exactly what that tier means.
"""

from __future__ import annotations

import csv
import glob
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.repository.eval_items import EvalItemRepo

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from sqlalchemy.orm import Session


SUITE = "spider2_lite_local_v1"
# Bumped when the materialized gold changes shape, not just when the upstream
# dataset does: 2026-08-06 typed the CSV gold and attached the benchmark's
# grading annotations, so runs graded before and after are not against the
# same materialization.
DATASET_VERSION = "spider2-lite-local-2026-08-06"


@dataclass(frozen=True)
class Spider2Task:
    """One Spider 2.0-lite local task in canonical form."""

    instance_id: str
    db_id: str
    question: str
    external_knowledge: str = ""
    gold_sql: str = ""
    # Every acceptable result, each as {"columns": [...], "rows": [[...], ...]}.
    accepted_results: list[dict[str, object]] = field(default_factory=list)
    # Grading annotations the benchmark publishes per instance
    # (evaluation_suite/gold/spider2lite_eval.jsonl). They are item data, not
    # grader logic: whether row order carries meaning, and which gold columns
    # the benchmark's own grader scores (one index list per accepted result).
    ignore_order: bool | None = None
    condition_cols: list[list[int]] = field(default_factory=list)


_INT_RE = re.compile(r"[-+]?\d+\Z")
_FLOAT_RE = re.compile(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?\Z")


def _type_csv_rows(rows: list[list[str]]) -> list[list[object]]:
    """Type CSV cells column-wise, the way the benchmark's own pandas read does.

    A column whose every non-empty cell parses as an integer becomes integers;
    failing that, floats; anything else stays text. Empty cells become NULL.
    Grading compares typed values, so leaving gold stringly would fail ``42``
    against ``"42"`` on every numeric column. Cell-wise typing would be wrong
    the other way: one stray annotation in a numeric column must demote the
    whole column, exactly as pandas reads it.
    """
    if not rows:
        return []
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        return [list(row) for row in rows]  # ragged: keep text rather than guess
    typed_columns: list[list[object]] = []
    for cells in zip(*rows, strict=True):
        stripped = [cell.strip() for cell in cells]
        non_empty = [cell for cell in stripped if cell != ""]
        cast: type[int] | type[float] | None = None
        if non_empty and all(_INT_RE.match(cell) for cell in non_empty):
            cast = int
        elif non_empty and all(_FLOAT_RE.match(cell) for cell in non_empty):
            cast = float
        typed_columns.append(
            [None if cell == "" else (cast(cell) if cast else cell) for cell in stripped]
        )
    return [list(row) for row in zip(*typed_columns, strict=True)]


def _read_csv_table(path: Path) -> dict[str, object] | None:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except (OSError, UnicodeDecodeError):
        return None
    if not rows:
        return None
    return {"columns": rows[0], "rows": _type_csv_rows(rows[1:])}


def _read_eval_annotations(repo_dir: Path) -> dict[str, dict[str, object]]:
    """Read per-instance grading annotations the benchmark ships with its gold.

    ``spider2lite_eval.jsonl`` carries ``ignore_order`` and ``condition_cols``
    for every instance. Dropping them grades stricter than the benchmark's own
    evaluator, so they are part of the gold, not an optional extra: a missing
    file is an error, the same as a missing task file.
    """
    path = repo_dir / "evaluation_suite" / "gold" / "spider2lite_eval.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"Spider 2.0-lite eval annotations not found: {path}")
    annotations: dict[str, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        annotations[str(raw.get("instance_id", ""))] = raw
    return annotations


def _normalize_condition_cols(
    instance_id: str, raw: object, accepted_count: int
) -> list[list[int]]:
    """Mirror the benchmark evaluator's own normalization, one list per gold.

    ``compare_multi_pandas_table`` accepts two shapes: a list of lists is
    positional (one entry per accepted result), while a FLAT list of indices
    applies to every accepted result. ``None``/``[]``/``[[]]``/``[None]`` all
    mean "every column counts". Anything else would restrict the wrong table's
    columns in silence, so it refuses instead.
    """
    if raw in (None, [], [[]], [None]):
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{instance_id}: condition_cols is not a list: {raw!r}")
    if all(isinstance(entry, list) for entry in raw):
        if len(raw) != accepted_count:
            raise ValueError(
                f"{instance_id}: {len(raw)} condition_cols entries for "
                f"{accepted_count} accepted results -- annotation/CSV mismatch"
            )
        return [[int(i) for i in entry] for entry in raw]
    if all(isinstance(entry, int) and not isinstance(entry, bool) for entry in raw):
        return [[int(i) for i in raw] for _ in range(accepted_count)]
    raise ValueError(f"{instance_id}: mixed condition_cols shape: {raw!r}")


def load_spider2_tasks(
    repo_dir: Path,
    *,
    selected_dbs: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[Spider2Task]:
    """Return local Spider 2.0-lite tasks from an extracted ``spider2-lite`` repo dir."""
    jsonl = repo_dir / "spider2-lite.jsonl"
    if not jsonl.exists():
        raise FileNotFoundError(f"Spider 2.0-lite task file not found: {jsonl}")
    annotations = _read_eval_annotations(repo_dir)

    selected = set(selected_dbs) if selected_dbs else None
    docs = repo_dir / "resource" / "documents"
    gold_sql_dir = repo_dir / "evaluation_suite" / "gold" / "sql"
    exec_dir = repo_dir / "evaluation_suite" / "gold" / "exec_result"

    tasks: list[Spider2Task] = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        instance_id = str(raw["instance_id"])
        if not instance_id.startswith("local"):
            continue
        db_id = str(raw["db"])
        if selected is not None and db_id not in selected:
            continue

        knowledge = ""
        doc_name = raw.get("external_knowledge")
        if doc_name:
            doc_path = docs / str(doc_name)
            if doc_path.is_file():
                knowledge = doc_path.read_text(encoding="utf-8").strip()

        sql_path = gold_sql_dir / f"{instance_id}.sql"
        gold_sql = sql_path.read_text(encoding="utf-8").strip() if sql_path.is_file() else ""

        paths = sorted(
            set(glob.glob(str(exec_dir / f"{instance_id}.csv")))
            | set(glob.glob(str(exec_dir / f"{instance_id}_*.csv")))
        )
        accepted = [t for t in (_read_csv_table(Path(p)) for p in paths) if t is not None]

        note = annotations.get(instance_id, {})
        ignore_order = note.get("ignore_order")

        tasks.append(
            Spider2Task(
                instance_id=instance_id,
                db_id=db_id,
                question=str(raw["question"]),
                external_knowledge=knowledge,
                gold_sql=gold_sql,
                accepted_results=accepted,
                ignore_order=bool(ignore_order) if ignore_order is not None else None,
                condition_cols=_normalize_condition_cols(
                    instance_id, note.get("condition_cols"), len(accepted)
                ),
            )
        )
        if limit is not None and len(tasks) >= limit:
            break
    return tasks


@dataclass(frozen=True)
class IngestResult:
    """Counts returned by :func:`ingest_spider2_tasks`."""

    inserted: int = 0
    skipped: int = 0
    refreshed: int = 0


def ingest_spider2_tasks(
    session: Session,
    *,
    team_id: UUID,
    tasks: Iterable[Spider2Task],
    created_by: UUID,
) -> IngestResult:
    """Upsert Spider 2.0-lite local tasks into ``eval_items``.

    Existing rows (by ``question_hash``) whose content matches are counted
    under ``skipped``; rows whose materialization differs (typed gold, new
    annotations) get a NEW VERSION under the same ``item_id`` -- the old
    version closes, results keep joining, and history keeps both readings.
    Re-running the script is always safe.
    """
    items_repo = EvalItemRepo(session)
    inserted = 0
    skipped = 0
    refreshed = 0
    for task in tasks:
        question_hash = f"spider2-lite:local:{task.instance_id}"
        item_input = {
            "db_id": task.db_id,
            "question": task.question,
            # The per-question reference document, where the benchmark ships one.
            # Named to match BIRD's field so a consumer reads one key, not two.
            "evidence": task.external_knowledge,
        }
        gold_answer = {
            "accepted_results": task.accepted_results,
            # Which gold columns the benchmark's evaluator scores, one index
            # list per accepted result. Empty means "all columns count".
            "condition_cols": task.condition_cols,
            # Documentation only: published for a minority of cases, and never the
            # thing graded against.
            "sql": task.gold_sql,
        }
        item_metadata = {
            "source": "spider2-lite-local",
            "instance_id": task.instance_id,
            "accepted_result_count": len(task.accepted_results),
            "has_gold_sql": bool(task.gold_sql),
            "has_external_knowledge": bool(task.external_knowledge),
            # The Tolerance.for_item seam: curated row-order opinion from the
            # benchmark itself outranks the ORDER BY heuristic at grading.
            **(
                {"tolerance": {"row_order_insensitive": task.ignore_order}}
                if task.ignore_order is not None
                else {}
            ),
        }
        item, created = items_repo.upsert_by_question_hash(
            # The accepted answers come from executing the benchmark's own queries.
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
    return IngestResult(inserted=inserted, skipped=skipped, refreshed=refreshed)
