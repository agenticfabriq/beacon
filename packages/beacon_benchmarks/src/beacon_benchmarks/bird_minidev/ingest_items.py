"""Load BIRD Mini-Dev raw tasks and ingest them into beacon eval_items."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.provenance import ActorType
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.provenance import ProvenanceRepo

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.orm import Session


SUITE = "bird_minidev_v2"
DATASET_VERSION = "v2-2025-07-22"
INGEST_ACTOR_ID = "scripts/ingest_bird_questions.py"
INGEST_REASON = "bird-minidev-v2 public-source ingest"


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
    """Upsert BIRD tasks into ``eval_items`` and write matching provenance rows.

    Existing rows (by ``question_hash``) are left untouched and counted under
    ``skipped`` so re-running the script is safe.
    """
    items_repo = EvalItemRepo(session)
    prov_repo = ProvenanceRepo(session)
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
                "source": "bird-minidev-v2",
                "difficulty": task.difficulty,
                "question_id": task.question_id,
            },
            created_by=created_by,
        )
        if not created:
            skipped += 1
            continue
        prov_repo.append(
            item_id=item.item_id,
            team_id=team_id,
            prior_tier=None,
            new_tier=EvalItemTier.HUMAN_VERIFIED,
            actor_type=ActorType.SYSTEM,
            actor_id=INGEST_ACTOR_ID,
            created_by=created_by,
            reason=INGEST_REASON,
            evidence={
                "question_id": task.question_id,
                "dataset_version": DATASET_VERSION,
                "db_id": task.db_id,
            },
        )
        inserted += 1
    return IngestResult(inserted=inserted, skipped=skipped)
