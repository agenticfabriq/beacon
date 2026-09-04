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


def _sweep_row(
    session: Session,
    fixtures: _SweepFixtures,
    runner: _ScriptedRunner,
    *,
    n_items: int,
    k: int = 1,
) -> object:
    """The persisted row itself, for assertions about what it recorded."""
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
        K=k,
        suite_id=fixtures.suite_id,
        team_id=fixtures.team_id,
        solution_id=fixtures.solution_id,
        harness_runner=cast("_HarnessRunnerLike", runner),
    )
    assert len(attributions) == 1
    return attributions[0]


def test_an_asymmetric_exclusion_is_counted_not_just_correct(
    session: Session,
    sweep_fixtures: object,
) -> None:
    """The delta is right and the sample it came from was halved (B64).

    `test_an_errored_item_does_not_fabricate_a_layer_delta` above asserts the
    delta is 0.0, which is correct: both arms tie on the item both could grade.
    What no column recorded is that the tie was measured over ONE item of two,
    and that the arms were not even. A layer whose ablation systematically
    breaks the endpoint would report a confident 0.0 over a sample it had
    quietly shrunk, exactly the failure `metrics.py` warns about in
    `gradeable_results`: "Report the number of excluded items alongside the
    rate, or a mostly-broken run looks healthy."
    """
    fixtures = cast("_SweepFixtures", sweep_fixtures)
    runner = _ScriptedRunner(
        script={
            "baseline": ["PASS", "PASS"],
            "no_ontology": ["PASS", "ERROR"],
        }
    )

    row = _sweep_row(session, fixtures, runner, n_items=2)

    assert row.n_items_submitted == 2  # type: ignore[attr-defined]
    assert row.n_baseline_excluded == 0  # type: ignore[attr-defined]
    assert row.n_ablated_excluded == 1  # type: ignore[attr-defined]
    assert row.n_compared == 1  # type: ignore[attr-defined]


def test_the_compared_count_travels_with_every_per_k_delta(
    session: Session,
    sweep_fixtures: object,
) -> None:
    """A rate and the n it was computed over must not be readable apart.

    The columns carry the HEADLINE k only, the way `ci_low` and `mcnemar_p`
    already do. Every other k lives in the JSONB, so the count lives there too
    -- otherwise reading `delta_pass_at_k["2"]` gives a number with no sample
    size anywhere near it. Run at K=3 for exactly that reason: at K=1 there is
    only the headline entry, and a writer that stamped nothing else would pass.
    """
    fixtures = cast("_SweepFixtures", sweep_fixtures)
    runner = _ScriptedRunner(
        script={
            "baseline": ["PASS", "PASS"],
            "no_ontology": ["PASS", "ERROR"],
        }
    )

    row = _sweep_row(session, fixtures, runner, n_items=2, k=3)

    # EVERY k, not just the headline. A writer that stamps only the promoted
    # entry leaves the other rates with no sample size anywhere near them.
    for key in ("delta_pass_at_k", "delta_pass_hat_k"):
        per_k = cast("dict[str, dict[str, float]]", getattr(row, key))
        assert set(per_k) == {"1", "2", "3"}, key
        for k, entry in per_k.items():
            assert entry["n_compared"] == 1, f"{key} at k={k}"


def test_a_sweep_that_compared_nothing_says_so(
    session: Session,
    sweep_fixtures: object,
) -> None:
    """delta 0.0 with a [0, 0] CI is the most confident claim the schema can make.

    `bootstrap_paired_ci` returns exactly that for an empty intersection and
    `mcnemar_exact` returns p=1.0, so a sweep where every item errored is
    stored as "this layer provably does nothing". The counts are what make that
    distinguishable from a real tie: n_compared 0 against 2 submitted.
    """
    fixtures = cast("_SweepFixtures", sweep_fixtures)
    runner = _ScriptedRunner(
        script={
            "baseline": ["ERROR", "ERROR"],
            "no_ontology": ["ERROR", "ERROR"],
        }
    )

    row = _sweep_row(session, fixtures, runner, n_items=2)

    assert row.n_compared == 0  # type: ignore[attr-defined]
    assert row.n_items_submitted == 2  # type: ignore[attr-defined]
    assert row.n_baseline_excluded == 2  # type: ignore[attr-defined]
    assert row.n_ablated_excluded == 2  # type: ignore[attr-defined]
