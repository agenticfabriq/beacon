"""Deterministic canned-result SUT for harness self-tests."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from beacon_runner.types import (
    ExecutionResult,
    ExecutionStep,
    Layer,
    SolutionIdentity,
)

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_runner.types import EvalItem, SolutionConfig


class DummySUT:
    """Canned SUT whose pass probability is calibrated by layer config."""

    VERSION = "0.2.0"
    BASELINE_PROB: float = 0.72
    LAYER_DELTAS: dict[str, float] = {
        "ontology": -0.14,
        "retry_loop": -0.06,
    }

    def __init__(self, *, owner_team_id: UUID) -> None:
        self._owner_team_id = owner_team_id

    def identity(self) -> SolutionIdentity:
        """Return DummySUT's static solution identity and layer manifest."""
        return SolutionIdentity(
            solution_id="dummy",
            version=self.VERSION,
            owner_team=self._owner_team_id,
            summary="Deterministic canned results for harness self-test.",
            supported_modes=["EVAL", "NIGHTLY_LOO"],
            layers=self.layers(),
        )

    def layers(self) -> list[Layer]:
        """Describe the simulated ``ontology`` and ``retry_loop`` layers."""
        return [
            Layer(
                name="ontology",
                description="Ontology resolution layer (simulated).",
                ablation_semantic="When off, pass probability drops by ~14 points.",
                instrumentation="synthetic",
            ),
            Layer(
                name="retry_loop",
                description="Self-correct retry loop (simulated).",
                ablation_semantic="When off, pass probability drops by ~6 points.",
                instrumentation="synthetic",
            ),
        ]

    def validate_config(self, config: SolutionConfig) -> list[str]:
        """Reject layer names outside the simulated ``ontology``/``retry_loop`` set."""
        known = {layer.name for layer in self.layers()}
        unknown = set(config.layers_enabled) - known
        if unknown:
            return [f"Unknown layers: {sorted(unknown)}"]
        return []

    def invoke(self, item: EvalItem, config: SolutionConfig) -> ExecutionResult:
        """Deterministically sample pass/fail from a layer-adjusted probability."""
        prob = self.BASELINE_PROB
        for layer_name, delta in self.LAYER_DELTAS.items():
            if not config.is_layer_enabled(layer_name):
                prob += delta

        seed_material = (
            f"{item.item_id}:{sorted(config.layers_enabled.items())}:{config.prompt_version}"
        )
        digest = hashlib.sha256(seed_material.encode()).digest()
        sample = int.from_bytes(digest[:4], "big") / (2**32)
        passed = sample < prob

        trace = ExecutionStep(
            uuid=f"root-{item.item_id}",
            name="dummy_invoke",
            level="workflow",
            status="COMPLETED",
            inputs={"item_id": item.item_id},
            outputs={"passed": passed, "prob": prob},
            children=[
                ExecutionStep(
                    uuid=f"ontology-{item.item_id}",
                    name="ontology_resolve",
                    level="layer:ontology",
                    status="COMPLETED" if config.is_layer_enabled("ontology") else "SKIPPED",
                ),
                ExecutionStep(
                    uuid=f"retry-{item.item_id}",
                    name="retry_loop",
                    level="layer:retry_loop",
                    status="COMPLETED" if config.is_layer_enabled("retry_loop") else "SKIPPED",
                ),
            ],
        )

        return ExecutionResult(
            output={"passed": passed, "answer": "yes" if passed else "no"},
            output_kind="answer",
            trace=trace,
            tokens_input=1000,
            tokens_output=200 if config.is_layer_enabled("retry_loop") else 100,
            runtime_ms=500,
        )
