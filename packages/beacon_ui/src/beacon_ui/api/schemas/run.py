"""Pydantic schemas for project run APIs."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from beacon_storage.models.runs import HarnessMode  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solution_id: UUID
    suite_id: UUID
    mode: HarnessMode
    # Runner configs are SUT-defined JSON objects, so the API accepts arbitrary values here.
    config: dict[str, Any] = Field(default_factory=dict)


class RunInvalidateIn(BaseModel):
    """Why a run is being retired. Required: the reason is the point."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class RunSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pass_at_1: float | None = None
    pass_at_3: float | None = None
    pass_at_5: float | None = None
    pass_hat_3: float | None = None
    median_tokens: float | None = None
    median_latency_ms: float | None = None
    n_items: int
    # Items whose every attempt errored. They are excluded from the pass rates
    # above, so this count is what keeps a mostly-broken run from reading well.
    n_errors: int = 0
    # Items the solution declined to answer. Unlike n_errors these stay in the
    # pass-rate denominator: declining is an outcome, not a missing measurement.
    n_deferred: int = 0


class RunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    project_id: UUID
    solution_id: UUID
    suite_id: UUID
    mode: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # Which ablation arm this run is, and the sweep grouping its siblings.
    # Without these the arm is only recoverable by diffing config.layers_enabled
    # against a baseline you must separately identify.
    parent_sweep_id: UUID | None = None
    sweep_arm: str | None = None
    # Set when the run has been retired. The row stays and keeps its results;
    # it just stops counting. A reader has to be able to see that and why.
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None
    summary: RunSummaryOut
