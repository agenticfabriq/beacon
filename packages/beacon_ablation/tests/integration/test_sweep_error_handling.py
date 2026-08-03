"""Infra errors must not manufacture a layer attribution (B12)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from beacon_ablation.engine import AttributionEngine
from beacon_storage.ids import uuid7

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from beacon_ablation.engine import _ConfigLike, _HarnessRunnerLike, _SutLike
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class _Row:
    item_id: str
    attempt_idx: int
    outcome: str
    tokens_input: int = 100
    tokens_output: int = 50
    runtime_ms: int = 10


@dataclass
class _ScriptedRunner:
    """Returns outcomes scripted per sweep arm, so errors can be placed exactly."""

    script: dict[str, list[str]]
    _by_run: dict[UUID, list[_Row]] = field(default_factory=dict)

    def run_single(
        self,
        *,
        sut: object,
        config: object,
        items: Sequence[object],
        suite: str,
        dataset_version: str,
        mode: str,
        pass_idx: int,
        parent_sweep_id: UUID,
        suite_id: UUID,
        team_id: UUID,
        solution_id: UUID,
        sweep_arm: str,
    ) -> UUID:
        _ = (sut, config, suite, dataset_version, mode, parent_sweep_id)
        _ = (suite_id, team_id, solution_id)
        run_id = uuid7()
        outcomes = self.script[sweep_arm]
        self._by_run[run_id] = [
            _Row(item_id=f"item-{idx}", attempt_idx=pass_idx, outcome=outcome)
            for idx, outcome in enumerate(outcomes[: len(items)])
        ]
        return run_id

    def list_results_for_run(self, run_id: UUID) -> list[_Row]:
        return self._by_run[run_id]


class _SweepFixtures(Protocol):
    sut: object
    items: list[object]
    suite_id: UUID
    team_id: UUID
    solution_id: UUID


def _sweep(
    session: Session,
    fixtures: _SweepFixtures,
    runner: _ScriptedRunner,
    *,
    n_items: int,
) -> dict[str, float]:
    from beacon_runner.types import SolutionConfig

    attributions = AttributionEngine(session).sweep(
        sut=cast("_SutLike", fixtures.sut),
        base_config=cast(
            "_ConfigLike",
            SolutionConfig(
                model_id="dummy-m",
                prompt_version="v0",
                layers_enabled={"ontology": True},
            ),
        ),
        items=fixtures.items[:n_items],
        suite="dummy-suite",
        dataset_version="v1",
        K=1,
        suite_id=fixtures.suite_id,
        team_id=fixtures.team_id,
        solution_id=fixtures.solution_id,
        harness_runner=cast("_HarnessRunnerLike", runner),
    )
    assert len(attributions) == 1
    row = attributions[0]
    per_k = cast("dict[str, dict[str, float]]", row.delta_pass_at_k)
    return {
        "delta": per_k["1"]["delta"],
        "baseline": cast("dict[str, float]", row.pass_at_k_baseline)["1"],
        "ablated": cast("dict[str, float]", row.pass_at_k_ablated)["1"],
    }


def test_an_errored_item_does_not_fabricate_a_layer_delta(
    session: Session,
    sweep_fixtures: object,
) -> None:
    """Both arms truly tie; one arm merely hit an endpoint error on item-1."""
    fixtures = cast("_SweepFixtures", sweep_fixtures)
    runner = _ScriptedRunner(
        script={
            "baseline": ["PASS", "PASS"],
            "no_ontology": ["PASS", "ERROR"],
        }
    )

    result = _sweep(session, fixtures, runner, n_items=2)

    # Counting the error as a failure would score the ablated arm 0.5 against a
    # baseline of 1.0 -- a fabricated -0.5 "the layer matters" signal.
    assert result["ablated"] == pytest.approx(1.0)
    assert result["baseline"] == pytest.approx(1.0)
    assert result["delta"] == pytest.approx(0.0)


def test_a_real_layer_effect_still_registers(
    session: Session,
    sweep_fixtures: object,
) -> None:
    """Excluding errors must not flatten genuine differences."""
    fixtures = cast("_SweepFixtures", sweep_fixtures)
    runner = _ScriptedRunner(
        script={
            "baseline": ["PASS", "PASS"],
            "no_ontology": ["PASS", "FAIL"],
        }
    )

    result = _sweep(session, fixtures, runner, n_items=2)

    assert result["baseline"] == pytest.approx(1.0)
    assert result["ablated"] == pytest.approx(0.5)
    assert result["delta"] == pytest.approx(0.5)


def test_errors_in_both_arms_drop_the_item_from_the_comparison(
    session: Session,
    sweep_fixtures: object,
) -> None:
    """An item nobody could grade contributes to neither arm."""
    fixtures = cast("_SweepFixtures", sweep_fixtures)
    runner = _ScriptedRunner(
        script={
            "baseline": ["PASS", "ERROR"],
            "no_ontology": ["FAIL", "ERROR"],
        }
    )

    result = _sweep(session, fixtures, runner, n_items=2)

    # Only item-0 is gradeable in both arms: baseline passes, ablated fails.
    assert result["baseline"] == pytest.approx(1.0)
    assert result["ablated"] == pytest.approx(0.0)
    assert result["delta"] == pytest.approx(1.0)
