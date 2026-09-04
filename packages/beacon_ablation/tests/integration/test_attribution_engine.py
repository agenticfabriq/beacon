"""Integration tests for AttributionEngine.sweep."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import pytest
from sqlalchemy import select

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_ablation.engine import _ConfigLike, _HarnessRunnerLike, _SutLike
    from beacon_storage.models.attribution import Attribution
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _SweepCall(Protocol):
    parent_sweep_id: UUID


class _SweepRunner(Protocol):
    calls: list[_SweepCall]
    arms: list[str]


class _SweepFixtures(Protocol):
    sut: object
    base_config: object
    items: list[object]
    runner: _SweepRunner
    suite_id: UUID
    team_id: UUID
    solution_id: UUID


def _run_sweep(
    session: Session,
    sweep_fixtures: object,
    *,
    k: int,
) -> list[Attribution]:
    from beacon_ablation.engine import AttributionEngine

    fixtures = cast("_SweepFixtures", sweep_fixtures)
    return AttributionEngine(session).sweep(
        sut=cast("_SutLike", fixtures.sut),
        base_config=cast("_ConfigLike", fixtures.base_config),
        items=fixtures.items,
        suite="dummy-suite",
        dataset_version="v1",
        K=k,
        suite_id=fixtures.suite_id,
        team_id=fixtures.team_id,
        solution_id=fixtures.solution_id,
        harness_runner=cast("_HarnessRunnerLike", fixtures.runner),
    )


def test_sweep_persists_one_row_per_layer(
    session: Session,
    sweep_fixtures: object,
) -> None:
    from beacon_storage.models.attribution import Attribution

    attributions = _run_sweep(session, sweep_fixtures, k=3)

    assert len(attributions) == 2
    rows = list(session.scalars(select(Attribution)))
    assert len(rows) == 2
    assert {row.layer_name for row in rows} == {"ontology", "retry_loop"}


def test_sweep_delta_pass_at_k_has_per_k_structure(
    session: Session,
    sweep_fixtures: object,
) -> None:
    """`n_compared` is part of the entry, not an optional extra.

    Every one of `delta`, `ci_low`, `ci_high` and `p` is computed over the
    paired intersection of task ids, so the size of that intersection belongs
    beside them -- a rate and its sample size read apart is how a delta over
    nothing passes for a tie (B64).
    """
    attributions = _run_sweep(session, sweep_fixtures, k=3)

    for attr in attributions:
        delta_pass_at_k = cast("dict[str, dict[str, float]]", attr.delta_pass_at_k)
        assert set(delta_pass_at_k) == {"1", "2", "3"}
        for entry in delta_pass_at_k.values():
            assert set(entry) == {"delta", "ci_low", "ci_high", "p", "n_compared"}
            # A real count, not a placeholder: this fixture errors on nothing,
            # so every submitted item is compared at every k.
            assert entry["n_compared"] == len(cast("_SweepFixtures", sweep_fixtures).items)


def test_sweep_applies_bh_correction(
    session: Session,
    sweep_fixtures: object,
) -> None:
    attributions = _run_sweep(session, sweep_fixtures, k=3)

    for attr in attributions:
        assert attr.bh_adjusted_p is not None
        assert float(attr.bh_adjusted_p) >= float(attr.mcnemar_p) - 1e-12


def test_sweep_runner_calls_share_parent_sweep_id(
    session: Session,
    sweep_fixtures: object,
) -> None:
    fixtures = cast("_SweepFixtures", sweep_fixtures)

    attributions = _run_sweep(session, sweep_fixtures, k=2)

    sweep_id = attributions[0].sweep_id
    assert len(fixtures.runner.calls) == 6
    assert {call.parent_sweep_id for call in fixtures.runner.calls} == {sweep_id}
    for attr in attributions:
        assert attr.sweep_id == sweep_id
        assert attr.baseline_run_id != attr.ablated_run_id


def test_sweep_labels_every_arm_it_dispatches(
    session: Session,
    sweep_fixtures: object,
) -> None:
    """Each arm is named on the way out, so persisting runners can tell them apart."""
    fixtures = cast("_SweepFixtures", sweep_fixtures)

    _run_sweep(session, sweep_fixtures, k=2)

    # 3 arms x K=2 passes, each pass carrying its own arm label.
    assert len(fixtures.runner.arms) == 6
    assert sorted(set(fixtures.runner.arms)) == ["baseline", "no_ontology", "no_retry_loop"]
    for arm in ("baseline", "no_ontology", "no_retry_loop"):
        assert fixtures.runner.arms.count(arm) == 2
