"""Schemas for reading a run's per-item results.

This is the read side of ingestion. A run's summary says how well a system did;
these say *which* questions it got wrong and why, which is the only way to act
on a score.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict


class ResultRowOut(BaseModel):
    """One item in a run, at the density a list needs."""

    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    question: str
    difficulty: str | None = None
    outcome: str | None = None
    status: str
    # The tolerant reading beside the outcome; None when no verdict carries it.
    got_facts: bool | None = None
    # Row counts come from the execution grader's verdict, so they are absent
    # for suites graded another way rather than defaulted to zero.
    candidate_row_count: int | None = None
    gold_row_count: int | None = None
    mismatch_kind: str | None = None
    tokens_input: int
    tokens_output: int
    runtime_ms: int
    created_at: datetime | None = None


class ResultListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ResultRowOut]
    total: int
    # Counts over the whole run, not the current page, so a filtered view can
    # still show what it is a slice of.
    outcome_counts: dict[str, int]
    difficulty_counts: dict[str, int]


class VerdictOut(BaseModel):
    """What one grader concluded, and the evidence it kept."""

    model_config = ConfigDict(extra="forbid")

    grader: str
    grader_version: str
    metric: str | None = None
    criterion: str
    passed: bool | None = None
    value: float | None = None
    justification: str | None = None
    # Grader-shaped: the SQL grader keeps both queries, both column lists, a
    # bounded row sample from each side, and which dimension mismatched.
    evidence: dict[str, Any] | None = None


class ResultDetailOut(BaseModel):
    """One item's answer beside the gold it was graded against."""

    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    run_id: UUID
    question: str
    evidence: str | None = None
    difficulty: str | None = None
    database: str | None = None
    outcome: str | None = None
    status: str
    # The tolerant reading beside the outcome; None when no verdict carries it.
    got_facts: bool | None = None
    deferred: bool
    error: str | None = None
    output: dict[str, Any]
    gold: dict[str, Any]
    tokens_input: int
    tokens_output: int
    runtime_ms: int
    created_at: datetime | None = None
    verdicts: list[VerdictOut]
