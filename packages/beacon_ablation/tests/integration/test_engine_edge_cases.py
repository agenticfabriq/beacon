"""Edge cases for AttributionEngine.sweep."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import numpy as np
import pytest
from beacon_ablation.errors import InsufficientDataError
from beacon_runner.types import SolutionConfig

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from beacon_ablation.engine import _ConfigLike, _HarnessRunnerLike, _SutLike
    from beacon_storage.models.attribution import Attribution
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _SweepFixtures(Protocol):
    sut: object
    base_config: object
    items: list[object]
    runner: object
    suite_id: UUID
    team_id: UUID
    solution_id: UUID


def _run_sweep(
    session: Session,
    sweep_fixtures: object,
    *,
    items: Sequence[object] | None = None,
    base_config: object | None = None,
    k: int = 2,
    rng: np.random.Generator | None = None,
) -> list[Attribution]:
    from beacon_ablation.engine import AttributionEngine

    fixtures = cast("_SweepFixtures", sweep_fixtures)
    return AttributionEngine(session, rng=rng).sweep(
        sut=cast("_SutLike", fixtures.sut),
        base_config=cast("_ConfigLike", base_config or fixtures.base_config),
        items=fixtures.items if items is None else items,
        suite="dummy-suite",
        dataset_version="v1",
        K=k,
        suite_id=fixtures.suite_id,
        team_id=fixtures.team_id,
        solution_id=fixtures.solution_id,
        harness_runner=cast("_HarnessRunnerLike", fixtures.runner),
    )


def test_sweep_raises_on_empty_items(
    session: Session,
    sweep_fixtures: object,
) -> None:
    with pytest.raises(InsufficientDataError, match="empty"):
        _run_sweep(session, sweep_fixtures, items=[])


def test_sweep_raises_on_k_zero(
    session: Session,
    sweep_fixtures: object,
) -> None:
    with pytest.raises(ValueError, match="K must be >= 1"):
        _run_sweep(session, sweep_fixtures, k=0)


def test_sweep_raises_when_no_layers_to_ablate(
    session: Session,
    sweep_fixtures: object,
) -> None:
    config = SolutionConfig(
        model_id="dummy-m",
        prompt_version="recovery-v0",
        layers_enabled={"ontology": False, "retry_loop": False},
    )

    with pytest.raises(InsufficientDataError, match="enabled layer"):
        _run_sweep(session, sweep_fixtures, base_config=config)


def test_sweep_reproducible_with_seeded_rng(
    session: Session,
    sweep_fixtures: object,
) -> None:
    first = _run_sweep(
        session,
        sweep_fixtures,
        rng=np.random.default_rng(42),
    )
    first_ci = {
        attribution.layer_name: (float(attribution.ci_low), float(attribution.ci_high))
        for attribution in first
    }

    second = _run_sweep(
        session,
        sweep_fixtures,
        rng=np.random.default_rng(42),
    )
    second_ci = {
        attribution.layer_name: (float(attribution.ci_low), float(attribution.ci_high))
        for attribution in second
    }

    assert first_ci == second_ci
