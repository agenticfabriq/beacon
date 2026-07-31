"""Grader-side verdict types, separate from SQLAlchemy persistence rows."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class VerdictOutcome(StrEnum):
    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class Verdict(BaseModel):
    """One scored criterion emitted by a grader."""

    model_config = ConfigDict(extra="forbid")

    grader: str
    grader_version: str
    criterion: str
    bool_value: bool | None = None
    value: float | None = None
    justification: str = ""
    raw_output: dict[str, Any] | None = None
    canonical_answer: dict[str, Any] | None = None
    answer_hash: str | None = None
    confidence: float | None = None
