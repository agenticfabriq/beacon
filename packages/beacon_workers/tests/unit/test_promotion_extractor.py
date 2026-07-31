"""CandidateExtractor orchestrates novelty and judge over trace batches."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import numpy as np
from beacon_workers.promotion.extractor import (
    CandidateExtractor,
    EvalItemCandidate,
    TraceCandidate,
)
from beacon_workers.promotion.judge import JudgeResult


def _embedding(*values: float) -> np.ndarray:
    vector = np.array(values, dtype=np.float32)
    return vector / (np.linalg.norm(vector) + 1e-12)


def _candidate(question: str = "q") -> TraceCandidate:
    return TraceCandidate(
        trace_id=uuid4(),
        team_id=uuid4(),
        solution_id="acme-chat-to-data",
        question=question,
        output={"sql": "SELECT 1"},
        metadata={},
    )


def test_extractor_drops_duplicates() -> None:
    judge = MagicMock()
    judge.evaluate.return_value = JudgeResult(
        kept=True,
        score=0.9,
        reason="ok",
        suggested_suite="s",
        raw={},
    )
    embedder = MagicMock()
    embedder.embed.return_value = [_embedding(1, 0, 0, 0)]
    extractor = CandidateExtractor(
        embedder=embedder,
        judge=judge,
        novelty_threshold=0.95,
        existing_embeddings=np.stack([_embedding(1, 0, 0, 0)]),
    )

    survivors = extractor.extract([_candidate()])

    assert survivors == []
    judge.evaluate.assert_not_called()


def test_extractor_keeps_novel_and_well_formed() -> None:
    judge = MagicMock()
    judge.evaluate.return_value = JudgeResult(
        kept=True,
        score=0.9,
        reason="ok",
        suggested_suite="ad_hoc",
        raw={},
    )
    embedder = MagicMock()
    embedder.embed.return_value = [_embedding(0, 1, 0, 0)]
    extractor = CandidateExtractor(
        embedder=embedder,
        judge=judge,
        novelty_threshold=0.95,
        existing_embeddings=np.stack([_embedding(1, 0, 0, 0)]),
    )

    survivors = extractor.extract([_candidate()])

    assert len(survivors) == 1
    assert isinstance(survivors[0], EvalItemCandidate)
    assert survivors[0].suggested_suite == "ad_hoc"


def test_extractor_drops_novel_but_not_well_formed() -> None:
    judge = MagicMock()
    judge.evaluate.return_value = JudgeResult(
        kept=False,
        score=0.3,
        reason="meta-question",
        suggested_suite=None,
        raw={},
    )
    embedder = MagicMock()
    embedder.embed.return_value = [_embedding(0, 1, 0, 0)]
    extractor = CandidateExtractor(
        embedder=embedder,
        judge=judge,
        novelty_threshold=0.95,
        existing_embeddings=np.stack([_embedding(1, 0, 0, 0)]),
    )

    survivors = extractor.extract([_candidate()])

    assert survivors == []


def test_extractor_handles_empty_batch() -> None:
    extractor = CandidateExtractor(
        embedder=MagicMock(),
        judge=MagicMock(),
        novelty_threshold=0.95,
        existing_embeddings=np.zeros((0, 4), dtype=np.float32),
    )

    assert extractor.extract([]) == []


def test_extractor_batches_embedding_call() -> None:
    judge = MagicMock()
    judge.evaluate.return_value = JudgeResult(
        kept=False,
        score=0.1,
        reason="r",
        suggested_suite=None,
        raw={},
    )
    embedder = MagicMock()
    embedder.embed.return_value = [
        _embedding(0, 1, 0, 0),
        _embedding(0, 0, 1, 0),
        _embedding(0, 0, 0, 1),
    ]
    extractor = CandidateExtractor(
        embedder=embedder,
        judge=judge,
        novelty_threshold=0.95,
        existing_embeddings=np.zeros((0, 4), dtype=np.float32),
    )

    extractor.extract(
        [
            _candidate("a"),
            _candidate("b"),
            _candidate("c"),
        ]
    )

    embedder.embed.assert_called_once()
    assert len(embedder.embed.call_args.kwargs["texts"]) == 3
