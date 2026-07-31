"""Anti-Goodhart worker tick loop."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from beacon_storage.ids import uuid7
from beacon_storage.repository.antigoodhart import AntigoodhartRepo
from beacon_storage.repository.eval_items import EvalItemRepo

from beacon_workers.antigoodhart.heuristics import (
    distribution_skew,
    evidence_leak,
    metadata_bleed,
    sql_in_question,
)
from beacon_workers.antigoodhart.heuristics.sql_in_question import (
    EvalItemView,
    FindingDraft,
    ScanContext,
)
from beacon_workers.logging import get_logger
from beacon_workers.watermark import WatermarkManager

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from beacon_storage.models.eval_items import EvalItem
    from sqlalchemy.orm import Session, sessionmaker


_WORKER_NAME = "antigoodhart"
_ITEM_HEURISTICS: tuple[
    Callable[[EvalItemView, ScanContext], list[FindingDraft]],
    ...,
] = (sql_in_question.scan, evidence_leak.scan, metadata_bleed.scan)


class AntiGoodhartWorker:
    """Run append-only anti-Goodhart scans over active eval items."""

    def __init__(
        self,
        *,
        factory: sessionmaker[Session],
        sql_ngram_size: int,
        team_share_threshold: float,
        batch_size: int,
    ) -> None:
        self.factory = factory
        self.ctx = ScanContext(
            sql_ngram_size=sql_ngram_size,
            team_share_threshold=team_share_threshold,
        )
        self.batch_size = batch_size
        self.log = get_logger().bind(worker=_WORKER_NAME)

    def tick(self) -> None:
        """Scan active eval items for anti-Goodhart findings and advance the watermark."""
        scan_id = uuid7()
        by_suite: dict[UUID, dict[UUID, int]] = defaultdict(lambda: defaultdict(int))
        n_findings = 0

        with self.factory() as session:
            item_repo = EvalItemRepo(session)
            finding_repo = AntigoodhartRepo(session)

            for batch in item_repo.iter_active_with_suite_ids(page_size=self.batch_size):
                for item, suite_id in batch:
                    n_findings += self._scan_item(
                        repo=finding_repo,
                        scan_id=scan_id,
                        item=item,
                        suite_id=suite_id,
                    )
                    if suite_id is not None and item.team_id is not None:
                        by_suite[suite_id][item.team_id] += 1

            for suite_id, team_counts in by_suite.items():
                for draft in distribution_skew.scan_suite(
                    suite_id=suite_id,
                    team_counts=dict(team_counts),
                    threshold=self.ctx.team_share_threshold,
                ):
                    _record_draft(
                        repo=finding_repo,
                        scan_id=scan_id,
                        draft=draft,
                    )
                    n_findings += 1

            WatermarkManager(
                session,
                worker_name=_WORKER_NAME,
                team_id=None,
            ).advance(last_processed_id=scan_id)
            session.commit()

        self.log.info(
            "antigoodhart.tick.completed",
            scan_id=str(scan_id),
            findings=n_findings,
        )

    def _scan_item(
        self,
        *,
        repo: AntigoodhartRepo,
        scan_id: UUID,
        item: EvalItem,
        suite_id: UUID | None,
    ) -> int:
        view = EvalItemView(
            id=item.item_id,
            team_id=item.team_id,
            suite_id=suite_id,
            question=_question_text(item),
            gold_output=item.gold_answer or {},
            evidence=_evidence_payload(item),
            metadata=item.item_metadata or {},
        )
        n_findings = 0
        for heuristic in _ITEM_HEURISTICS:
            for draft in heuristic(view, self.ctx):
                _record_draft(repo=repo, scan_id=scan_id, draft=draft)
                n_findings += 1
        return n_findings


def _question_text(item: EvalItem) -> str:
    question = item.item_input.get("question")
    return question if isinstance(question, str) else ""


def _evidence_payload(item: EvalItem) -> dict[str, object] | None:
    if item.evidence is None:
        return None
    return {"evidence": item.evidence}


def _record_draft(
    *,
    repo: AntigoodhartRepo,
    scan_id: UUID,
    draft: FindingDraft,
) -> None:
    repo.record(
        scan_id=scan_id,
        item_id=draft.item_id,
        team_id=draft.team_id,
        suite_id=draft.suite_id,
        kind=draft.kind,
        severity=draft.severity,
        description=draft.description,
        evidence=draft.evidence,
    )
