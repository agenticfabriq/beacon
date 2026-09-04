"""AttributionEngine orchestration for leave-one-out sweeps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

import numpy as np
from beacon_storage.config_identity import config_digest, model_id_of
from beacon_storage.ids import uuid7
from beacon_storage.models.attribution import Attribution

from beacon_ablation.ablator import Ablator
from beacon_ablation.errors import InsufficientDataError
from beacon_ablation.metrics import (
    gradeable_results,
    median_runtime_ms,
    median_total_tokens,
    per_task_pass_at_k,
    per_task_pass_hat_k,
    restrict_to_k_attempts,
    suite_pass_at_k,
    suite_pass_hat_k,
)
from beacon_ablation.stats import (
    benjamini_hochberg,
    bootstrap_paired_ci,
    mcnemar_exact,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.orm import Session


class _IdentityLike(Protocol):
    version: str


class _ConfigLike(Protocol):
    layers_enabled: dict[str, bool]
    model_id: str

    def model_copy(self, *, deep: bool = False) -> _ConfigLike:
        """Return a (deep) copy of the config so its layers_enabled can be mutated."""
        ...

    def model_dump(self) -> dict[str, Any]:
        """Return the config as a mapping, for the identity a sweep is measured under."""
        ...


class _SutLike(Protocol):
    def identity(self) -> _IdentityLike:
        """Return the SUT identity stamped onto persisted attribution rows."""
        ...

    def layers(self) -> Sequence[object]:
        """Return the SUT's declared layer descriptors."""
        ...


class _HarnessRunnerLike(Protocol):
    def run_single(
        self,
        *,
        sut: _SutLike,
        config: _ConfigLike,
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
        """Execute one harness pass over items and return the persisted run id.

        ``sweep_arm`` is the arm label this config represents (``baseline`` or
        ``no_<layer>``). Runners that persist runs need it to tell the arms of a
        single sweep apart -- every other identifying field is shared.
        """
        ...

    def list_results_for_run(self, run_id: UUID) -> Sequence[object]:
        """Return per-item result rows for a previously executed run."""
        ...


class _PerKEntry(TypedDict):
    delta: float
    ci_low: float
    ci_high: float
    p: float
    # The paired task count these three were computed over. `bootstrap_paired_ci`
    # and `mcnemar_exact` both reduce to the INTERSECTION of task ids, and both
    # answer "no effect" for an empty one -- (0.0, 0.0, 0.0) and p=1.0. Without
    # this, a delta of zero over nothing is indistinguishable from a real tie.
    n_compared: int


def per_k_count(entry: object, field_name: str) -> int | None:
    """Read a COUNT out of a per-k attribution entry, as an int or as absent.

    Public and shared because "what an absent count means" must be answered
    once. A float reader with a 0.0 fallback -- the obvious thing to reach for,
    since the deltas beside it are floats -- gets both halves wrong: the count
    prints as ``2.0`` next to integer columns, and a missing one renders as
    ``0.0``, which is also the value that means nothing was compared. That is
    the collapse these counts exist to prevent.

    ``bool`` is rejected: it passes ``isinstance(value, int)`` and would read
    ``True`` as a sample of one.
    """
    if isinstance(entry, Mapping):
        value = entry.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


class AttributionEngine:
    """Computes and persists attribution rows for NIGHTLY_LOO sweeps."""

    def __init__(self, session: Session, *, rng: np.random.Generator | None = None) -> None:
        self.session = session
        self.rng = rng if rng is not None else np.random.default_rng()
        self.ablator = Ablator()

    def sweep(
        self,
        *,
        sut: _SutLike,
        base_config: _ConfigLike,
        items: Sequence[object],
        suite: str,
        dataset_version: str,
        K: int,
        suite_id: UUID,
        team_id: UUID,
        solution_id: UUID,
        harness_runner: _HarnessRunnerLike,
    ) -> list[Attribution]:
        """Run baseline plus per-layer LOO configs and persist attributions."""
        if K < 1:
            raise ValueError(f"K must be >= 1, got {K}")
        if not items:
            raise InsufficientDataError("sweep called with empty items list")

        sweep_id = uuid7()
        configs = self.ablator.enumerate_loo_configs(base_config, sut=sut)
        if len(configs) < 2:
            raise InsufficientDataError("sweep needs at least one enabled layer to ablate")

        runs_by_label: dict[str, list[UUID]] = {}
        results_by_label: dict[str, list[object]] = {}
        for label, config in configs:
            runs_by_label[label] = []
            results_by_label[label] = []
            for pass_idx in range(K):
                run_id = harness_runner.run_single(
                    sut=sut,
                    config=config,
                    items=items,
                    suite=suite,
                    dataset_version=dataset_version,
                    mode="NIGHTLY_LOO",
                    pass_idx=pass_idx,
                    parent_sweep_id=sweep_id,
                    suite_id=suite_id,
                    team_id=team_id,
                    solution_id=solution_id,
                    sweep_arm=label,
                )
                runs_by_label[label].append(run_id)
                results_by_label[label].extend(harness_runner.list_results_for_run(run_id))

        identity = sut.identity()
        # The identity every row in this sweep was measured under. A layer
        # effect is a claim about one model and one configuration.
        base_config_payload = base_config.model_dump()
        baseline_results = results_by_label["baseline"]
        attributions: list[Attribution] = []
        for label, _config in configs:
            if label == "baseline":
                continue
            layer_name = label.removeprefix("no_")
            row = self._build_attribution(
                sweep_id=sweep_id,
                team_id=team_id,
                solution_id=solution_id,
                solution_version=identity.version,
                model_id=model_id_of(base_config_payload),
                config_digest=config_digest(base_config_payload),
                suite=suite,
                dataset_version=dataset_version,
                layer_name=layer_name,
                K=K,
                baseline_results=baseline_results,
                ablated_results=results_by_label[label],
                baseline_run_id=runs_by_label["baseline"][0],
                ablated_run_id=runs_by_label[label][0],
                n_items_submitted=len(items),
            )
            self.session.add(row)
            attributions.append(row)

        self.session.flush()
        self._apply_bh_correction(attributions)
        self.session.flush()
        return attributions

    def _build_attribution(
        self,
        *,
        sweep_id: UUID,
        team_id: UUID,
        solution_id: UUID,
        solution_version: str,
        model_id: str | None,
        config_digest: str | None,
        suite: str,
        dataset_version: str,
        layer_name: str,
        K: int,
        baseline_results: list[object],
        ablated_results: list[object],
        baseline_run_id: UUID,
        ablated_run_id: UUID,
        n_items_submitted: int,
    ) -> Attribution:
        pass_at_k_baseline: dict[str, float | None] = {}
        pass_at_k_ablated: dict[str, float | None] = {}
        delta_pass_at_k: dict[str, _PerKEntry] = {}
        pass_hat_k_baseline: dict[str, float | None] = {}
        pass_hat_k_ablated: dict[str, float | None] = {}
        delta_pass_hat_k: dict[str, _PerKEntry] = {}
        # Per k, because `restrict_to_k_attempts` drops a different set at each
        # one. `per_task_pass_at_k` and `per_task_pass_hat_k` both key off
        # `_group_by_task`, so their task ids are identical at a given k and one
        # set of counts describes both readings.
        sample_at_k: dict[str, tuple[int, int, int]] = {}

        # An ERROR is the harness or the endpoint failing, not the layer. Left in,
        # an endpoint blip during one arm depresses that arm and manufactures a
        # delta -- an infra hiccup reading as "this layer matters".
        baseline_graded = gradeable_results(baseline_results)
        ablated_graded = gradeable_results(ablated_results)

        for k in range(1, K + 1):
            baseline_k = restrict_to_k_attempts(baseline_graded, k=k)
            ablated_k = restrict_to_k_attempts(ablated_graded, k=k)
            baseline_at_k = per_task_pass_at_k(baseline_k, k=k)
            ablated_at_k = per_task_pass_at_k(ablated_k, k=k)
            n_compared = len(set(baseline_at_k) & set(ablated_at_k))
            sample_at_k[str(k)] = (
                n_items_submitted - len(baseline_at_k),
                n_items_submitted - len(ablated_at_k),
                n_compared,
            )
            pass_at_k_baseline[str(k)] = suite_pass_at_k(baseline_k, k=k)
            pass_at_k_ablated[str(k)] = suite_pass_at_k(ablated_k, k=k)
            delta, ci_low, ci_high = bootstrap_paired_ci(
                baseline_at_k,
                ablated_at_k,
                n_resamples=10_000,
                rng=self.rng,
            )
            p = mcnemar_exact(baseline_at_k, ablated_at_k)
            delta_pass_at_k[str(k)] = {
                "delta": delta,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "p": p,
                "n_compared": n_compared,
            }

            baseline_hat_k = per_task_pass_hat_k(baseline_k, k=k)
            ablated_hat_k = per_task_pass_hat_k(ablated_k, k=k)
            pass_hat_k_baseline[str(k)] = suite_pass_hat_k(baseline_k, k=k)
            pass_hat_k_ablated[str(k)] = suite_pass_hat_k(ablated_k, k=k)
            delta_hat, hat_ci_low, hat_ci_high = bootstrap_paired_ci(
                baseline_hat_k,
                ablated_hat_k,
                n_resamples=10_000,
                rng=self.rng,
            )
            p_hat = mcnemar_exact(baseline_hat_k, ablated_hat_k)
            delta_pass_hat_k[str(k)] = {
                "delta": delta_hat,
                "ci_low": hat_ci_low,
                "ci_high": hat_ci_high,
                "p": p_hat,
                "n_compared": n_compared,
            }

        # Cost is measured on attempts that did the work; an errored attempt
        # records whatever it spent before failing and would skew both medians.
        token_delta_pct = self._relative_delta(
            median_total_tokens(baseline_graded),
            median_total_tokens(ablated_graded),
        )
        runtime_delta_pct = self._relative_delta(
            median_runtime_ms(baseline_graded),
            median_runtime_ms(ablated_graded),
        )

        headline_k = str(min(3, K))
        headline = delta_pass_at_k[headline_k]
        baseline_excluded, ablated_excluded, headline_compared = sample_at_k[headline_k]
        return Attribution(
            attribution_id=uuid7(),
            sweep_id=sweep_id,
            team_id=team_id,
            solution_id=solution_id,
            solution_version=solution_version,
            model_id=model_id,
            config_digest=config_digest,
            suite=suite,
            dataset_version=dataset_version,
            layer_name=layer_name,
            methodology="LOO",
            baseline_run_id=baseline_run_id,
            ablated_run_id=ablated_run_id,
            pass_at_k_baseline=pass_at_k_baseline,
            pass_at_k_ablated=pass_at_k_ablated,
            delta_pass_at_k=delta_pass_at_k,
            pass_hat_k_baseline=pass_hat_k_baseline,
            pass_hat_k_ablated=pass_hat_k_ablated,
            delta_pass_hat_k=delta_pass_hat_k,
            n_items_submitted=n_items_submitted,
            n_baseline_excluded=baseline_excluded,
            n_ablated_excluded=ablated_excluded,
            n_compared=headline_compared,
            token_delta_pct=token_delta_pct,
            runtime_delta_pct=runtime_delta_pct,
            mcnemar_p=headline["p"],
            bh_adjusted_p=None,
            ci_low=headline["ci_low"],
            ci_high=headline["ci_high"],
        )

    def _apply_bh_correction(self, attributions: list[Attribution]) -> None:
        if not attributions:
            return
        adjusted = benjamini_hochberg([float(row.mcnemar_p) for row in attributions])
        for row, p_adjusted in zip(attributions, adjusted, strict=True):
            row.bh_adjusted_p = p_adjusted

    def _relative_delta(self, baseline: float | None, ablated: float | None) -> float | None:
        if baseline is None or ablated is None or baseline <= 0.0:
            return None
        return (ablated - baseline) / baseline
