"""Pydantic schemas for attribution APIs."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict


class AttributionLayerOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer: str
    delta_pass_at_3: float
    ci_low: float
    ci_high: float
    mcnemar_p: float
    bh_p: float
    median_token_delta_pct: float | None = None


class AttributionSnapshotOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solution_id: UUID
    suite_id: UUID
    supported: bool
    computed_at: datetime | None = None
    layers: list[AttributionLayerOut]
