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
    # What the delta was measured over. Recording the exclusion without
    # publishing it leaves the rate lying by omission, which was the finding:
    # a layer whose ablation breaks the endpoint reports a confident delta over
    # a sample it quietly shrank, and an uneven shrink means the two arms are
    # no longer the same set of tasks. `None` for a row computed before the
    # counts existed -- absent, not zero, because zero would claim nothing was
    # excluded.
    n_compared: int | None = None
    n_items_submitted: int | None = None
    n_baseline_excluded: int | None = None
    n_ablated_excluded: int | None = None


class AttributionSnapshotOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solution_id: UUID
    suite_id: UUID
    supported: bool
    computed_at: datetime | None = None
    layers: list[AttributionLayerOut]
