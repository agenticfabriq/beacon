"""Schemas for pushing execution outputs into a registered run."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResultIngestIn(BaseModel):
    """One item's raw execution output, pushed by whoever ran the solution.

    Deliberately carries **outputs, not verdicts**: beacon grades server-side so
    the system under test never scores its own work. Everything here is
    SUT-asserted, including tokens and runtime; only the verdicts beacon
    computes from it are beacon's own claim.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=200)
    attempt_idx: int = Field(default=0, ge=0)
    output: dict[str, Any]
    output_kind: str = "json"
    # Omitted means unmeasured, not free. A runner whose format carries no
    # per-item cost must not be made to assert zero.
    tokens_input: int | None = Field(default=None, ge=0)
    tokens_output: int | None = Field(default=None, ge=0)
    runtime_ms: int = Field(default=0, ge=0)
    # The solution was asked and declined. Composes to DEFER, not FAIL.
    deferred: bool = False
    # Set when the attempt could not run. Composes to ERROR, and the item
    # leaves the pass-rate denominator rather than counting against the model.
    error: str | None = None
    trace: dict[str, Any] | None = None


class ResultIngestOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    attempt_idx: int
    outcome: str
    # False when this exact payload was already ingested, so a retrying client
    # that lost our response can push again without a spurious failure.
    created: bool


class RunCompleteOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    n_results: int
