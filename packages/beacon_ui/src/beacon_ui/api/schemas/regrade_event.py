"""Schemas for the History API -- what a regrade changed, and why."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict


class RegradeEventOut(BaseModel):
    """One recorded regrade, as the History list shows it."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    recorded_at: datetime
    grader: str
    grader_version: str
    headline_metric: str

    n_runs: int
    n_graded: int
    n_flipped: int

    # Free text in the operator's words, and `None` when they gave none.
    # Rendered unedited: it is an explanation, not a measurement, and the
    # display has to tolerate an operator's imprecision rather than imply the
    # string was computed.
    reason: str | None = None


class RegradeEventRunOut(BaseModel):
    """What one run's PASS count did across the event.

    ``pass_before`` is arithmetic, not a stored value: ``pass_now`` minus the
    gains plus the losses. That only holds while ``pass_now`` is read in the
    same breath as the event, which is why this is computed per request rather
    than cached anywhere.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    model_id: str | None = None
    config_label: str | None = None

    pass_now: int
    gained: int
    lost: int
    pass_before: int


class RegradeEventDetailOut(BaseModel):
    """One event with its per-run breakdown."""

    model_config = ConfigDict(extra="forbid")

    event: RegradeEventOut
    suite_name: str

    # Only runs whose outcomes actually moved. An event records flips only, so
    # a run absent from this list was either untouched or unaffected -- and the
    # two are different, which is what `runs_affected` against the event's
    # `n_runs` is for.
    runs: list[RegradeEventRunOut]
    runs_affected: int

    # Every transition the event recorded, as `"FAIL->PASS": 298`. Both
    # directions are published because a regrade that only ever raised numbers
    # is a property of one event, not of regrades, and a reader cannot tell
    # which they are looking at from a single total.
    transitions: dict[str, int]
