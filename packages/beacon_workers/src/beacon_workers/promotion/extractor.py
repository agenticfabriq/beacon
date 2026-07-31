"""Candidate extractor orchestration for promotion traces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from beacon_workers.promotion.novelty import NoveltyFilter

if TYPE_CHECKING:
    from uuid import UUID

    import numpy as np

    from beacon_workers.promotion.judge import JudgeResult


@dataclass(frozen=True)
class TraceCandidate:
    """Production trace candidate normalized for promotion filtering."""

    trace_id: UUID
    team_id: UUID
    solution_id: str
    question: str
    output: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class EvalItemCandidate:
    """Promotion survivor ready for insertion as a model-proposed eval item."""

    trace_id: UUID
    team_id: UUID
    solution_id: str
    question: str
    output: dict[str, Any]
    metadata: dict[str, Any]
    embedding: np.ndarray
    novelty_max_sim: float
    judge_score: float
    judge_reason: str
    suggested_suite: str | None


class _Embedder(Protocol):
    def embed(self, *, texts: list[str]) -> list[np.ndarray]:
        """Return one embedding vector per input text."""
        ...


class _Judge(Protocol):
    def evaluate(self, *, question: str, output: dict[str, Any]) -> JudgeResult:
        """Score a candidate question/output pair for promotion eligibility."""
        ...


class CandidateExtractor:
    def __init__(
        self,
        *,
        embedder: _Embedder,
        judge: _Judge,
        novelty_threshold: float,
        existing_embeddings: np.ndarray,
    ) -> None:
        self.embedder = embedder
        self.judge = judge
        self.novelty = NoveltyFilter(
            existing_embeddings=existing_embeddings,
            threshold=novelty_threshold,
        )

    def extract(self, candidates: list[TraceCandidate]) -> list[EvalItemCandidate]:
        """Embed, novelty-filter, and judge candidates, returning the survivors."""
        if not candidates:
            return []

        embeddings = self.embedder.embed(texts=[candidate.question for candidate in candidates])
        survivors: list[EvalItemCandidate] = []
        for candidate, embedding in zip(candidates, embeddings, strict=True):
            keep, max_similarity = self.novelty.is_novel(embedding)
            if not keep:
                continue

            result = self.judge.evaluate(question=candidate.question, output=candidate.output)
            if not result.kept:
                continue

            survivors.append(
                EvalItemCandidate(
                    trace_id=candidate.trace_id,
                    team_id=candidate.team_id,
                    solution_id=candidate.solution_id,
                    question=candidate.question,
                    output=candidate.output,
                    metadata=candidate.metadata,
                    embedding=embedding,
                    novelty_max_sim=max_similarity,
                    judge_score=result.score,
                    judge_reason=result.reason,
                    suggested_suite=result.suggested_suite,
                )
            )
        return survivors
