"""Pure convergence voting over canonical answer hashes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True)
class SutVerdict:
    """One SUT's comparable answer verdict for one eval item."""

    sut_id: UUID
    answer_hash: str
    canonical_answer: dict[str, Any]
    confidence: float


@dataclass(frozen=True)
class ConvergenceVote:
    """Decision returned by the convergence voter."""

    promotable: bool
    winning_answer_hash: str | None
    winning_canonical_answer: dict[str, Any] | None
    supporting_sut_count: int
    reason: str


class ConvergenceVoter:
    """Promote only when enough distinct SUTs agree on one exact answer hash."""

    def __init__(self, *, min_suts: int, min_confidence: float) -> None:
        self.min_suts = min_suts
        self.min_confidence = min_confidence

    def vote(self, verdicts: list[SutVerdict]) -> ConvergenceVote:
        """Return the promotion decision for verdicts gated by min_suts and min_confidence."""
        if not verdicts:
            return ConvergenceVote(
                promotable=False,
                winning_answer_hash=None,
                winning_canonical_answer=None,
                supporting_sut_count=0,
                reason="no verdicts",
            )

        sut_ids_by_hash: dict[str, set[UUID]] = defaultdict(set)
        canonical_by_hash: dict[str, dict[str, Any]] = {}
        for verdict in verdicts:
            if verdict.confidence < self.min_confidence:
                continue
            sut_ids_by_hash[verdict.answer_hash].add(verdict.sut_id)
            canonical_by_hash.setdefault(verdict.answer_hash, verdict.canonical_answer)

        if not sut_ids_by_hash:
            return ConvergenceVote(
                promotable=False,
                winning_answer_hash=None,
                winning_canonical_answer=None,
                supporting_sut_count=0,
                reason=f"no verdicts cleared min_confidence={self.min_confidence}",
            )

        winning_hash, supporting_sut_ids = max(
            sut_ids_by_hash.items(),
            key=lambda item: len(item[1]),
        )
        supporting_sut_count = len(supporting_sut_ids)
        if supporting_sut_count < self.min_suts:
            return ConvergenceVote(
                promotable=False,
                winning_answer_hash=winning_hash,
                winning_canonical_answer=canonical_by_hash[winning_hash],
                supporting_sut_count=supporting_sut_count,
                reason=(
                    f"only {supporting_sut_count} of required {self.min_suts} distinct SUTs agree"
                ),
            )

        return ConvergenceVote(
            promotable=True,
            winning_answer_hash=winning_hash,
            winning_canonical_answer=canonical_by_hash[winning_hash],
            supporting_sut_count=supporting_sut_count,
            reason=f"{supporting_sut_count} distinct SUTs agree on hash={winning_hash[:12]}",
        )
