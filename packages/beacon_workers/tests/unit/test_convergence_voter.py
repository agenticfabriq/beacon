"""ConvergenceVoter exact-match canonical-answer voting."""

from __future__ import annotations

from uuid import UUID, uuid4

from beacon_workers.convergence.voter import ConvergenceVoter, SutVerdict


def _v(
    *,
    sut_id: UUID | None = None,
    answer_hash: str = "h1",
    confidence: float = 0.9,
) -> SutVerdict:
    return SutVerdict(
        sut_id=sut_id or uuid4(),
        answer_hash=answer_hash,
        canonical_answer={"hash": answer_hash},
        confidence=confidence,
    )


def test_three_suts_agree_promotes() -> None:
    voter = ConvergenceVoter(min_suts=3, min_confidence=0.85)

    result = voter.vote([_v(answer_hash="h1"), _v(answer_hash="h1"), _v(answer_hash="h1")])

    assert result.promotable is True
    assert result.winning_answer_hash == "h1"
    assert result.winning_canonical_answer == {"hash": "h1"}
    assert result.supporting_sut_count == 3


def test_two_agree_one_disagrees_no_promotion() -> None:
    voter = ConvergenceVoter(min_suts=3, min_confidence=0.85)

    result = voter.vote([_v(answer_hash="h1"), _v(answer_hash="h1"), _v(answer_hash="h2")])

    assert result.promotable is False
    assert result.winning_answer_hash == "h1"
    assert result.supporting_sut_count == 2
    assert "only 2" in result.reason.lower()


def test_low_confidence_excluded() -> None:
    voter = ConvergenceVoter(min_suts=3, min_confidence=0.85)

    result = voter.vote(
        [
            _v(answer_hash="h1", confidence=0.9),
            _v(answer_hash="h1", confidence=0.9),
            _v(answer_hash="h1", confidence=0.5),
        ]
    )

    assert result.promotable is False
    assert result.supporting_sut_count == 2


def test_split_majority_picks_largest_cluster() -> None:
    voter = ConvergenceVoter(min_suts=3, min_confidence=0.85)

    result = voter.vote(
        [
            _v(answer_hash="h1"),
            _v(answer_hash="h1"),
            _v(answer_hash="h1"),
            _v(answer_hash="h2"),
        ]
    )

    assert result.promotable is True
    assert result.winning_answer_hash == "h1"
    assert result.supporting_sut_count == 3


def test_duplicate_sut_counted_once() -> None:
    voter = ConvergenceVoter(min_suts=3, min_confidence=0.85)
    sut_id = uuid4()

    result = voter.vote(
        [
            _v(sut_id=sut_id, answer_hash="h1"),
            _v(sut_id=sut_id, answer_hash="h1"),
            _v(answer_hash="h1"),
        ]
    )

    assert result.promotable is False
    assert result.supporting_sut_count == 2


def test_empty_verdicts() -> None:
    voter = ConvergenceVoter(min_suts=3, min_confidence=0.85)

    result = voter.vote([])

    assert result.promotable is False
    assert result.winning_answer_hash is None
    assert result.supporting_sut_count == 0
    assert result.reason == "no verdicts"
