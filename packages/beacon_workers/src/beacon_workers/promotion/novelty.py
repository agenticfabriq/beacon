"""Cosine-similarity novelty filter for promotion candidates."""

from __future__ import annotations

import numpy as np


class NoveltyFilter:
    """Reject candidates that are too similar to existing eval items."""

    def __init__(self, *, existing_embeddings: np.ndarray, threshold: float) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be in [0, 1], got {threshold}")
        if existing_embeddings.ndim != 2:
            raise ValueError(
                f"existing_embeddings must be 2D, got shape {existing_embeddings.shape}"
            )
        self.existing = existing_embeddings.astype(np.float32, copy=False)
        self.threshold = threshold

    def is_novel(self, candidate: np.ndarray) -> tuple[bool, float]:
        """Return `(keep, max_similarity)` for one normalized candidate vector."""
        if candidate.ndim != 1:
            raise ValueError(f"candidate must be 1D, got shape {candidate.shape}")
        if self.existing.shape[0] == 0:
            return True, 0.0
        if self.existing.shape[1] != candidate.shape[0]:
            raise ValueError(
                f"dim mismatch: existing D={self.existing.shape[1]} "
                f"vs candidate D={candidate.shape[0]}"
            )

        similarities = self.existing @ candidate.astype(np.float32, copy=False)
        max_similarity = float(np.max(similarities))
        return max_similarity < self.threshold, max_similarity
