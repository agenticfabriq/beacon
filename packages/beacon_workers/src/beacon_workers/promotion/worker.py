"""Promotion-worker tick loop."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import numpy as np
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.provenance import ActorType
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.production_traces import ProductionTraceRepo
from beacon_storage.repository.provenance import ProvenanceRepo
from beacon_storage.repository.teams import TeamRepo

from beacon_workers.logging import get_logger
from beacon_workers.promotion.extractor import (
    CandidateExtractor,
    EvalItemCandidate,
    TraceCandidate,
)
from beacon_workers.watermark import WatermarkManager

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_storage.models.eval_items import EvalItem
    from sqlalchemy.orm import Session, sessionmaker

    from beacon_workers.promotion.judge import WellFormedJudge


_WORKER_NAME = "promotion"


class _Embedder(Protocol):
    name: str

    def embed(self, *, texts: list[str]) -> list[np.ndarray]:
        """Return one embedding vector per input text."""
        ...


def question_hash(*, team_id: UUID, question: str, suite_hint: str | None) -> str:
    """Return the natural key for a promoted production-trace question."""
    raw = f"{team_id}|{question}|{suite_hint or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PromotionWorker:
    """Transactional shell around candidate extraction and persistence."""

    def __init__(
        self,
        *,
        factory: sessionmaker[Session],
        embedder: _Embedder,
        judge: WellFormedJudge,
        novelty_threshold: float,
        batch_size: int,
        max_per_team_per_tick: int,
    ) -> None:
        self.factory = factory
        self.embedder = embedder
        self.judge = judge
        self.novelty_threshold = novelty_threshold
        self.batch_size = batch_size
        self.max_per_team_per_tick = max_per_team_per_tick
        self.log = get_logger().bind(worker=_WORKER_NAME)

    def tick_all_teams(self) -> None:
        """Run one promotion tick for every known team."""
        with self.factory() as session:
            team_ids = [team.id for team in TeamRepo(session).list()]

        for team_id in team_ids:
            try:
                self.tick_for_team(team_id=team_id)
            except Exception as exc:
                self.log.error(
                    "promotion.tick.team_failed",
                    team_id=str(team_id),
                    error=repr(exc),
                )
                with self.factory() as session:
                    WatermarkManager(
                        session,
                        worker_name=_WORKER_NAME,
                        team_id=team_id,
                    ).record_error(repr(exc))
                    session.commit()

    def tick_for_team(self, *, team_id: UUID) -> None:
        """Process one bounded batch of candidate production traces for a team."""
        with self.factory() as session:
            watermark = WatermarkManager(
                session,
                worker_name=_WORKER_NAME,
                team_id=team_id,
            )
            traces = ProductionTraceRepo(session).list_after(
                team_id=team_id,
                after_id=watermark.fetch_since(),
                limit=min(self.batch_size, self.max_per_team_per_tick),
            )
            if not traces:
                self.log.info("promotion.tick.no_new_traces", team_id=str(team_id))
                return

            existing_embeddings = _load_existing_embeddings(
                session=session,
                team_id=team_id,
            )
            extractor = CandidateExtractor(
                embedder=self.embedder,
                judge=self.judge,
                novelty_threshold=self.novelty_threshold,
                existing_embeddings=existing_embeddings,
            )
            survivors = extractor.extract(
                [
                    TraceCandidate(
                        trace_id=trace.id,
                        team_id=trace.team_id,
                        solution_id=trace.solution_id,
                        question=str(trace.item_input.get("question") or ""),
                        output=trace.item_output,
                        metadata=trace.trace_metadata,
                    )
                    for trace in traces
                ]
            )

            self._persist_survivors(
                session=session,
                team_id=team_id,
                survivors=survivors,
            )
            watermark.advance(last_processed_id=traces[-1].id)
            session.commit()

            self.log.info(
                "promotion.tick.completed",
                team_id=str(team_id),
                traces=len(traces),
                promoted=len(survivors),
                last_processed_id=str(traces[-1].id),
            )

    def _persist_survivors(
        self,
        *,
        session: Session,
        team_id: UUID,
        survivors: list[EvalItemCandidate],
    ) -> None:
        eval_repo = EvalItemRepo(session)
        provenance_repo = ProvenanceRepo(session)

        for candidate in survivors:
            item, inserted = eval_repo.upsert_by_question_hash(
                tier=EvalItemTier.MODEL_PROPOSED,
                suite=candidate.suggested_suite or "ad_hoc",
                team_id=team_id,
                solution_id=candidate.solution_id,
                dataset_version="production_trace_v1",
                item_input={"question": candidate.question},
                gold_answer=candidate.output,
                item_metadata=_item_metadata(
                    candidate=candidate,
                    embedding_provider=_embedding_provider_name(self.embedder),
                ),
                question_hash=question_hash(
                    team_id=candidate.team_id,
                    question=candidate.question,
                    suite_hint=candidate.suggested_suite,
                ),
                embedding=_embedding_list(candidate.embedding),
            )
            if inserted:
                _append_provenance(
                    provenance_repo=provenance_repo,
                    item=item,
                    team_id=team_id,
                    candidate=candidate,
                )


def _load_existing_embeddings(*, session: Session, team_id: UUID) -> np.ndarray:
    rows = [
        item.embedding
        for item in EvalItemRepo(session).list_active(team_id=team_id)
        if item.embedding is not None
    ]
    if not rows:
        return np.zeros((0, 0), dtype=np.float32)
    return np.asarray(rows, dtype=np.float32)


def _item_metadata(
    *,
    candidate: EvalItemCandidate,
    embedding_provider: str,
) -> dict[str, object]:
    return {
        **candidate.metadata,
        "source_trace_id": str(candidate.trace_id),
        "suggested_suite": candidate.suggested_suite,
        "judge_score": candidate.judge_score,
        "judge_reason": candidate.judge_reason,
        "novelty_max_sim": candidate.novelty_max_sim,
        "embedding_provider": embedding_provider,
        "promotion_at": datetime.now(UTC).isoformat(),
    }


def _embedding_provider_name(embedder: _Embedder) -> str:
    name = getattr(embedder, "name", "unknown")
    return name if isinstance(name, str) else "unknown"


def _embedding_list(embedding: np.ndarray) -> list[float]:
    return [float(value) for value in embedding.tolist()]


def _append_provenance(
    *,
    provenance_repo: ProvenanceRepo,
    item: EvalItem,
    team_id: UUID,
    candidate: EvalItemCandidate,
) -> None:
    provenance_repo.append(
        item_id=item.item_id,
        team_id=team_id,
        prior_tier=None,
        new_tier=EvalItemTier.MODEL_PROPOSED,
        actor_type=ActorType.SYSTEM,
        actor_id=_WORKER_NAME,
        created_by=None,
        reason=f"judge_score={candidate.judge_score:.2f}: {candidate.judge_reason}",
        evidence={
            "source_trace_id": str(candidate.trace_id),
            "novelty_max_sim": candidate.novelty_max_sim,
            "judge_score": candidate.judge_score,
        },
    )
