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

Tier is EXECUTION_CONFIRMED, not HUMAN_VERIFIED: the accepted answers were produced by
executing the benchmark's own queries, which is exactly what that tier means.
"""

from __future__ import annotations

import csv
import glob
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.repository.eval_items import EvalItemRepo

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from sqlalchemy.orm import Session


SUITE = "spider2_lite_local_v1"
DATASET_VERSION = "spider2-lite-local-2026-08-05"


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


def _read_csv_table(path: Path) -> dict[str, object] | None:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except (OSError, UnicodeDecodeError):
        return None
    if not rows:
        return None
    return {"columns": rows[0], "rows": rows[1:]}


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

        tasks.append(
            Spider2Task(
                instance_id=instance_id,
                db_id=db_id,
                question=str(raw["question"]),
                external_knowledge=knowledge,
                gold_sql=gold_sql,
                accepted_results=accepted,
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


def ingest_spider2_tasks(
    session: Session,
    *,
    team_id: UUID,
    tasks: Iterable[Spider2Task],
    created_by: UUID,
) -> IngestResult:
    """Upsert Spider 2.0-lite local tasks into ``eval_items``.

    Existing rows (by ``question_hash``) are left untouched and counted under
    ``skipped`` so re-running the script is safe.
    """
    items_repo = EvalItemRepo(session)
    inserted = 0
    skipped = 0
    for task in tasks:
        question_hash = f"spider2-lite:local:{task.instance_id}"
        _item, created = items_repo.upsert_by_question_hash(
            # The accepted answers come from executing the benchmark's own queries.
            tier=EvalItemTier.EXECUTION_CONFIRMED,
            suite=SUITE,
            team_id=team_id,
            dataset_version=DATASET_VERSION,
            question_hash=question_hash,
            item_input={
                "db_id": task.db_id,
                "question": task.question,
                # The per-question reference document, where the benchmark ships one.
                # Named to match BIRD's field so a consumer reads one key, not two.
                "evidence": task.external_knowledge,
            },
            gold_answer={
                "accepted_results": task.accepted_results,
                # Documentation only: published for a minority of cases, and never the
                # thing graded against.
                "sql": task.gold_sql,
            },
            item_metadata={
                "source": "spider2-lite-local",
                "instance_id": task.instance_id,
                "accepted_result_count": len(task.accepted_results),
                "has_gold_sql": bool(task.gold_sql),
                "has_external_knowledge": bool(task.external_knowledge),
            },
            created_by=created_by,
        )
        if not created:
            skipped += 1
            continue
        inserted += 1
    return IngestResult(inserted=inserted, skipped=skipped)
