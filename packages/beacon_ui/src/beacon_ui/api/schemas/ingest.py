"""Schemas for pushing execution outputs into a registered run."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    #
    # A zero that IS sent is believed. That differs on purpose from
    # `_usage_counts` in the judge provider, which distrusts a zeroed usage
    # block: there we are reading a third-party server that may auto-fill a
    # field it never measured, here a caller is asserting a number over our own
    # documented contract. Same value, different writer, different reading.
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


class RunCompleteIn(BaseModel):
    """The optional body of a run close, carrying WHY if it went badly.

    Optional in full: every pusher that exists posts no body, and closing a
    run cleanly must not start requiring one. Present with an `error`, it
    closes the run as FAILED instead -- which is the authority a pusher needs
    and did not have. `invalidate` does the same job but requires
    EVAL_MANAGE, so a TEAM_MEMBER pusher could push results and then had no
    way to end its own run except claiming success (B71).
    """

    model_config = ConfigDict(extra="forbid")

    error: str | None = None

    @field_validator("error")
    @classmethod
    def _reason_must_say_something(cls, value: str | None) -> str | None:
        """A blank reason is worse than none: it fails the run and explains nothing."""
        if value is None:
            return None
        if not value.strip():
            raise ValueError(
                "error must say why the run failed, or be omitted to close it as complete"
            )
        return value.strip()


class RunCompleteOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    n_results: int
