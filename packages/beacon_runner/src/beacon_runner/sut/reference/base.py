"""Shared scaffolding for the shipped reference SUTs."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from beacon_graders.llm.provider import JudgeRequest

from beacon_runner.types import ExecutionResult, ExecutionStep, SolutionIdentity

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_graders.llm.provider import LLMProvider

    from beacon_runner.types import EvalItem, Layer, SolutionConfig


class BaseReferenceSqlSUT:
    """Common protocol implementation for reference SQL-producing SUTs.

    Subclasses declare the ``SOLUTION_ID``, ``VERSION``, ``SUMMARY``, and
    ``LAYERS`` class attributes and override :meth:`_compose_prompt` /
    :meth:`_parse_output` to plug a real text-to-SQL framework. The base
    implementation drives a single layer-aware LLM call against the supplied
    ``LLMProvider`` and emits a trace whose children are tagged
    ``layer:<name>`` so attribution sweeps work unmodified.
    """

    SOLUTION_ID: str = "base-reference"
    VERSION: str = "0.1.0"
    SUMMARY: str = "Reference SQL-producing SUT scaffolding."
    LAYERS: tuple[Layer, ...] = ()

    def __init__(
        self,
        *,
        owner_team_id: UUID,
        llm_provider: LLMProvider,
    ) -> None:
        self._owner_team_id = owner_team_id
        self._llm_provider = llm_provider

    def identity(self) -> SolutionIdentity:
        """Return the reference SUT's solution identity and layer manifest."""
        return SolutionIdentity(
            solution_id=self.SOLUTION_ID,
            version=self.VERSION,
            owner_team=self._owner_team_id,
            summary=self.SUMMARY,
            supported_modes=["EVAL", "NIGHTLY_LOO"],
            layers=list(self.LAYERS),
        )

    def layers(self) -> list[Layer]:
        """Return this SUT's ablatable layer manifest."""
        return list(self.LAYERS)

    def validate_config(self, config: SolutionConfig) -> list[str]:
        """Reject layers_enabled keys outside this SUT's declared manifest."""
        known = {layer.name for layer in self.LAYERS}
        unknown = sorted(set(config.layers_enabled) - known)
        if unknown:
            return [f"Unknown layers: {unknown}"]
        if not config.model_id:
            return ["model_id is required"]
        return []

    def invoke(self, item: EvalItem, config: SolutionConfig) -> ExecutionResult:
        """Compose a layered prompt, call the LLM, and wrap the response in an ExecutionResult."""
        start_ms = int(time.monotonic() * 1000)
        enabled = {layer.name: config.is_layer_enabled(layer.name) for layer in self.LAYERS}
        prompt = self._compose_prompt(item=item, config=config, enabled_layers=enabled)
        request = JudgeRequest(
            prompt=prompt,
            grader_version=f"{self.SOLUTION_ID}:{self.VERSION}",
            system=self._compose_system_prompt(config=config),
        )
        response = self._llm_provider.generate(request)
        output = self._parse_output(item=item, raw_text=response.text)

        children: list[ExecutionStep] = []
        for layer in self.LAYERS:
            children.append(
                ExecutionStep(
                    uuid=f"{layer.name}-{item.item_id}",
                    name=layer.name,
                    level=f"layer:{layer.name}",
                    status="COMPLETED" if enabled[layer.name] else "SKIPPED",
                )
            )

        trace = ExecutionStep(
            uuid=f"root-{item.item_id}",
            name=f"{self.SOLUTION_ID}_invoke",
            level="workflow",
            status="COMPLETED",
            inputs={"item_id": item.item_id, "model_id": config.model_id},
            outputs={"output_kind": "sql"},
            children=children,
        )

        runtime_ms = max(int(time.monotonic() * 1000) - start_ms, 0)
        return ExecutionResult(
            output=output,
            output_kind="sql",
            trace=trace,
            tokens_input=response.tokens_input,
            tokens_output=response.tokens_output,
            runtime_ms=runtime_ms,
        )

    def _compose_system_prompt(self, *, config: SolutionConfig) -> str:
        """Return the system prompt; subclasses may override to inject framework guidance."""
        return f"You are {self.SOLUTION_ID} ({self.VERSION}). Answer concisely."

    def _compose_prompt(
        self,
        *,
        item: EvalItem,
        config: SolutionConfig,
        enabled_layers: dict[str, bool],
    ) -> str:
        """Compose the user-side prompt; subclasses must override to wire their framework."""
        question = str(item.query.get("question", "")).strip() or item.item_id
        db_id = str(item.query.get("db_id", "")).strip() or "unknown"
        layer_note = ", ".join(
            f"{name}={'on' if on else 'off'}" for name, on in sorted(enabled_layers.items())
        )
        return (
            f"Database: {db_id}\n"
            f"Question: {question}\n"
            f"Active layers: {layer_note}\n"
            "Return the SQL only."
        )

    def _parse_output(self, *, item: EvalItem, raw_text: str) -> dict[str, object]:
        """Wrap the LLM's raw text in the ExecutionResult.output shape."""
        return {"sql": raw_text.strip(), "item_id": item.item_id}
