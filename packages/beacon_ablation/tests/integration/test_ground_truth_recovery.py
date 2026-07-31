"""Ground-truth recovery test for Beacon's attribution pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import pytest

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_storage.models.attribution import Attribution

pytestmark = pytest.mark.integration

_EXPECTED_BASELINE_PROB = 0.72
_EXPECTED_LAYER_DELTAS = {"ontology": -0.14, "retry_loop": -0.06}
_EXPECTED_LAYER_DROPS = {layer_name: -delta for layer_name, delta in _EXPECTED_LAYER_DELTAS.items()}
_DELTA_TOLERANCE = 0.05
_LARGE_LAYER_MCNEMAR_P_MAX = 0.05


class _SweepFixtures(Protocol):
    project_id: UUID
    team_id: UUID
    solution_id: UUID


def _by_layer(attributions: list[Attribution]) -> dict[str, Attribution]:
    return {attribution.layer_name: attribution for attribution in attributions}


def _pass_at_1_delta(attribution: Attribution) -> float:
    entry = cast("dict[str, float]", attribution.delta_pass_at_k["1"])
    return entry["delta"]


def _pass_at_1_ci(attribution: Attribution) -> tuple[float, float]:
    entry = cast("dict[str, float]", attribution.delta_pass_at_k["1"])
    return entry["ci_low"], entry["ci_high"]


def test_dummy_sut_ground_truth_constants_unchanged() -> None:
    from beacon_runner.dummy_sut import DummySUT

    assert DummySUT.BASELINE_PROB == _EXPECTED_BASELINE_PROB
    assert DummySUT.LAYER_DELTAS == _EXPECTED_LAYER_DELTAS


def test_loo_recovers_ontology_drop_within_tolerance(
    recovery_sweep_result: list[Attribution],
) -> None:
    ontology = _by_layer(recovery_sweep_result)["ontology"]

    recovered = _pass_at_1_delta(ontology)

    assert abs(recovered - _EXPECTED_LAYER_DROPS["ontology"]) < _DELTA_TOLERANCE


def test_loo_recovers_retry_loop_drop_within_tolerance(
    recovery_sweep_result: list[Attribution],
) -> None:
    retry_loop = _by_layer(recovery_sweep_result)["retry_loop"]

    recovered = _pass_at_1_delta(retry_loop)

    assert abs(recovered - _EXPECTED_LAYER_DROPS["retry_loop"]) < _DELTA_TOLERANCE


def test_ontology_ci_brackets_true_drop(
    recovery_sweep_result: list[Attribution],
) -> None:
    ontology = _by_layer(recovery_sweep_result)["ontology"]

    ci_low, ci_high = _pass_at_1_ci(ontology)

    assert ci_low <= _EXPECTED_LAYER_DROPS["ontology"] <= ci_high


def test_retry_loop_ci_brackets_true_drop(
    recovery_sweep_result: list[Attribution],
) -> None:
    retry_loop = _by_layer(recovery_sweep_result)["retry_loop"]

    ci_low, ci_high = _pass_at_1_ci(retry_loop)

    assert ci_low <= _EXPECTED_LAYER_DROPS["retry_loop"] <= ci_high


def test_mcnemar_p_significant_for_larger_layer(
    recovery_sweep_result: list[Attribution],
) -> None:
    ontology = _by_layer(recovery_sweep_result)["ontology"]

    assert float(ontology.mcnemar_p) < _LARGE_LAYER_MCNEMAR_P_MAX


def test_bh_correction_present_and_inflates_p(
    recovery_sweep_result: list[Attribution],
) -> None:
    for attribution in recovery_sweep_result:
        assert attribution.bh_adjusted_p is not None
        assert float(attribution.bh_adjusted_p) >= float(attribution.mcnemar_p) - 1e-12


def test_attribution_rows_persisted_with_correct_tenancy(
    recovery_sweep_result: list[Attribution],
    sweep_fixtures: object,
) -> None:
    fixtures = cast("_SweepFixtures", sweep_fixtures)

    for attribution in recovery_sweep_result:
        assert attribution.project_id == fixtures.project_id
        assert attribution.team_id == fixtures.team_id
        assert attribution.solution_id == fixtures.solution_id


def test_pass_at_k_baseline_close_to_baseline_prob(
    recovery_sweep_result: list[Attribution],
) -> None:
    pass_at_1_values = [
        cast("dict[str, float]", attribution.pass_at_k_baseline)["1"]
        for attribution in recovery_sweep_result
    ]

    assert max(pass_at_1_values) - min(pass_at_1_values) < 1e-9
    assert abs(pass_at_1_values[0] - _EXPECTED_BASELINE_PROB) < 0.10
