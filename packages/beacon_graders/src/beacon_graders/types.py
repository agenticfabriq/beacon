"""Grader-side verdict types, separate from SQLAlchemy persistence rows."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class VerdictOutcome(StrEnum):
    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"
    # Asked, and declined to answer. Not a failure: counting it as one hides
    # over-deferral, which is the difference between a cautious system and a
    # wrong one. Stays in the denominator, unlike ERROR.
    DEFER = "DEFER"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class GraderKind(StrEnum):
    """What sort of evidence a grader produces.

    ``VerdictComposer`` precedence depends on this rather than on ``Grader.name``:
    benchmark adapters rename their grader instances (``bird_minidev_v2.exec_sql``
    and friends) so the name is a display label, not a stable classification.
    """

    EXECUTION = "execution"
    LLM_JUDGE = "llm_judge"


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
