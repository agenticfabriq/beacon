"""Pydantic schemas for project suite APIs."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, Literal
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field, model_validator

SuiteKind = Literal["manual", "curated"]
SuiteMethod = Literal["manual", "separability_gain", "handpicked"]


class SuiteCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kind: SuiteKind | None = None
    item_ids: list[UUID] = Field(default_factory=list)
    source_suite: str | None = None
    target_size: int | None = Field(None, ge=1, le=10000)
    description: str = ""
    method: SuiteMethod | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_mode(self) -> SuiteCreateIn:
        """Validate that kind and method are mutually consistent."""
        if self.kind == "curated" and not self.source_suite:
            raise ValueError("curated kind requires source_suite")
        if self.kind == "manual" and self.method == "separability_gain":
            raise ValueError("manual kind conflicts with separability_gain method")
        return self

    @property
    def resolved_kind(self) -> SuiteKind:
        """Return the suite kind, inferring from method when not set explicitly."""
        if self.kind is not None:
            return self.kind
        if self.method == "separability_gain":
            return "curated"
        return "manual"

    @property
    def resolved_method(self) -> SuiteMethod:
        """Return the suite method, inferring from kind when not set explicitly."""
        if self.resolved_kind == "curated":
            return "separability_gain"
        return self.method or "manual"


class SuiteOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The reference run every other run in this benchmark is read against.
    baseline_run_id: UUID | None = None

    suite_id: UUID
    id: UUID
    team_id: UUID
    name: str
    description: str
    method: SuiteMethod
    kind: SuiteKind
    metadata: dict[str, Any]
    created_by: UUID
    created_at: datetime
    item_count: int
    # Every run registered against this benchmark, INVALIDATED ONES INCLUDED,
    # and uncapped. Both halves of that are deliberate.
    #
    # Included, because it is what the UI already displayed: it counted runs
    # itself with `include_invalidated: true`, so bird_minidev_v2 reads 288
    # here while the results matrix aggregates 42 valid ones. Serving a
    # different number from the same column would have changed a figure on
    # screen as a side effect of making it cheaper. The gap is real and worth
    # surfacing one day; it is not this change's to decide.
    #
    # Uncapped, because the client asked for `limit: 500` and took `.length`,
    # so a benchmark past 500 runs would have under-reported in silence. A
    # COUNT has no such ceiling.
    run_count: int


class SuiteListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suites: list[SuiteOut]
    total: int


class SuitePatchIn(BaseModel):
    """Benchmark settings. baseline_run_id may be set to null to unpin."""

    model_config = ConfigDict(extra="forbid")

    baseline_run_id: UUID | None = None
