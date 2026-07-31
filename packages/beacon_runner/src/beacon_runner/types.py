"""Type surface shared across the runner, graders, and SUTs.

These are Pydantic v2 models: frozen where the value is a contract and mutable
where the harness builds them incrementally.

EvalItem is a P2 shim. P4 introduces the versioned ``eval_items`` table and
adds tier/dataset_version columns; the runtime shape stays compatible.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field

Instrumentation = Literal["native", "wrapped", "synthetic"]


class Layer(BaseModel):
    """One enumerable ablation layer a SUT declares."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1)
    ablation_semantic: str = Field(min_length=1)
    instrumentation: Instrumentation


class SolutionIdentity(BaseModel):
    """Identity declared by a SUT. ``layers`` may be empty."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    solution_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    owner_team: UUID
    summary: str = ""
    supported_modes: list[str] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)


class SolutionConfig(BaseModel):
    """Runtime config passed into ``invoke()``."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    prompt_version: str = "v0"
    layers_enabled: dict[str, bool] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    extras: dict[str, Any] = Field(default_factory=dict)

    def is_layer_enabled(self, name: str) -> bool:
        """Return whether layer ``name`` is enabled (default ``True`` when unset)."""
        return self.layers_enabled.get(name, True)


class ExecutionStep(BaseModel):
    """Hierarchical trace node."""

    model_config = ConfigDict(extra="forbid")

    uuid: str
    name: str
    level: str
    status: Literal["COMPLETED", "FAILED", "SKIPPED", "RUNNING"] = "COMPLETED"
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    children: list[ExecutionStep] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this step (and children) to a JSON-safe dict."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionStep:
        """Reconstruct an ``ExecutionStep`` tree from its serialized dict form."""
        return cls.model_validate(data)


class ExecutionResult(BaseModel):
    """What ``sut.invoke()`` returns."""

    model_config = ConfigDict(extra="forbid")

    output: dict[str, Any]
    output_kind: str = "json"
    trace: ExecutionStep
    tokens_input: int = 0
    tokens_output: int = 0
    runtime_ms: int = 0
    error: str | None = None


class EvalItem(BaseModel):
    """In-memory eval task."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    suite: str
    query: dict[str, Any]
    ground_truth: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
