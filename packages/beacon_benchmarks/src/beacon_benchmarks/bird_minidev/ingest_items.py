"""Load BIRD Mini-Dev raw tasks and ingest them into beacon eval_items."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.repository.eval_items import EvalItemRepo

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.orm import Session


SUITE = "bird_minidev_v2"
DATASET_VERSION = "v2-2025-07-22"


@dataclass(frozen=True)
class BirdTask:
    """One BIRD Mini-Dev task in canonical form."""

    question_id: int
    db_id: str
    question: str
    evidence: str
    sql: str
    difficulty: str


def load_bird_tasks(
    json_path: Path,
    *,
    selected_dbs: Sequence[str],
) -> list[BirdTask]:
    """Return BIRD tasks from ``json_path`` whose db_id is in ``selected_dbs``."""
    if not json_path.exists():
        raise FileNotFoundError(f"BIRD task JSON not found: {json_path}")
    selected = set(selected_dbs)
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    return [
        BirdTask(
            question_id=int(task["question_id"]),
            db_id=str(task["db_id"]),
            question=str(task["question"]),
            evidence=str(task.get("evidence") or ""),
            sql=str(task["SQL"]),
            difficulty=str(task.get("difficulty") or "unknown"),
        )
        for task in raw
        if str(task["db_id"]) in selected
    ]


@dataclass(frozen=True)
class IngestResult:
    """Counts returned by :func:`ingest_bird_tasks`."""

    inserted: int = 0
    skipped: int = 0


def ingest_bird_tasks(
    session: Session,
    *,
    team_id: UUID,
    tasks: Iterable[BirdTask],
    created_by: UUID,
) -> IngestResult:
    """Upsert BIRD tasks into ``eval_items``.

    Existing rows (by ``question_hash``) are left untouched and counted under
    ``skipped`` so re-running the script is safe.
    """
    items_repo = EvalItemRepo(session)
    inserted = 0
    skipped = 0
    for task in tasks:
        question_hash = f"bird:v2:{task.question_id}"
        item, created = items_repo.upsert_by_question_hash(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite=SUITE,
            team_id=team_id,
            dataset_version=DATASET_VERSION,
            question_hash=question_hash,
            item_input={
                "db_id": task.db_id,
                "question": task.question,
                "evidence": task.evidence,
            },
            gold_answer={"sql": task.sql},
            item_metadata={
                # Where this item came from. The provenance table this used to
                # write is gone: curated gold is audited in the semantic layer,
                # and for a public corpus dataset_version plus this say enough.
                "source": "bird-minidev-v2",
                "difficulty": task.difficulty,
                "question_id": task.question_id,
                # BIRD's published EX compares set(rows): duplicates collapse.
                # Declared here so the one grader matches the leaderboard's own
                # rule -- matched with eyes open, not endorsed (docs/grading.md).
                "tolerance": {"duplicate_rows_insignificant": True},
            },
            created_by=created_by,
        )
        if not created:
            skipped += 1
            continue
        inserted += 1
    return IngestResult(inserted=inserted, skipped=skipped)
