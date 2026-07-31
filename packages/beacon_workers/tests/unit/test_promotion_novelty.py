"""NoveltyFilter rejects near-duplicates by cosine similarity."""

from __future__ import annotations

import numpy as np
import pytest
from beacon_workers.promotion.novelty import NoveltyFilter


def _vec(*values: float) -> np.ndarray:
    vector = np.array(values, dtype=np.float32)
    return vector / (np.linalg.norm(vector) + 1e-12)


def test_empty_existing_passes_all() -> None:
    novelty = NoveltyFilter(
        existing_embeddings=np.zeros((0, 4), dtype=np.float32),
        threshold=0.95,
    )

    keep, max_sim = novelty.is_novel(_vec(1, 0, 0, 0))

    assert keep is True
    assert max_sim == 0.0


def test_exact_match_rejected() -> None:
    existing = np.stack([_vec(1, 0, 0, 0)])
    novelty = NoveltyFilter(existing_embeddings=existing, threshold=0.95)

    keep, max_sim = novelty.is_novel(_vec(1, 0, 0, 0))

    assert keep is False
    assert max_sim > 0.99


def test_orthogonal_passes() -> None:
    existing = np.stack([_vec(1, 0, 0, 0)])
    novelty = NoveltyFilter(existing_embeddings=existing, threshold=0.95)

    keep, max_sim = novelty.is_novel(_vec(0, 1, 0, 0))

    assert keep is True
    assert max_sim < 0.01


def test_near_duplicate_above_threshold_rejected() -> None:
    existing = np.stack([_vec(1, 0, 0, 0)])
    novelty = NoveltyFilter(existing_embeddings=existing, threshold=0.95)

    keep, max_sim = novelty.is_novel(_vec(0.96, 0.28, 0, 0))

    assert keep is False
    assert 0.95 < max_sim < 0.97


def test_just_under_threshold_kept() -> None:
    existing = np.stack([_vec(1, 0, 0, 0)])
    novelty = NoveltyFilter(existing_embeddings=existing, threshold=0.95)

    keep, _max_sim = novelty.is_novel(_vec(0.9, 0.436, 0, 0))

    assert keep is True


def test_finds_max_across_existing_set() -> None:
    existing = np.stack(
        [
            _vec(1, 0, 0, 0),
            _vec(0, 1, 0, 0),
            _vec(0.5, 0.5, 0.5, 0.5),
        ]
    )
    novelty = NoveltyFilter(existing_embeddings=existing, threshold=0.95)

    keep, max_sim = novelty.is_novel(_vec(0, 1, 0, 0))

    assert keep is False
    assert max_sim > 0.99


def test_threshold_validated() -> None:
    with pytest.raises(ValueError):
        NoveltyFilter(
            existing_embeddings=np.zeros((0, 4), dtype=np.float32),
            threshold=1.5,
        )
