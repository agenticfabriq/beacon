"""Run/Result/Verdict/Trace -- the execution-state tables.

Conventions:
- Every row carries denormalized team_id + project_id so RLS policies and
  project dashboards do not need joins through Run.
- Result.status tracks SUT-side completion (COMPLETED / ERROR / TIMEOUT).
- Result.outcome tracks grader-composed verdict (PASS / FAIL / ERROR / TIMEOUT).
- Trace is one-to-one with Result; we keep the entire step tree as JSONB.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import StrEnum
from uuid import UUID  # noqa: TC003

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, IdMixin, TimestampsMixin


class HarnessMode(StrEnum):
    """How a run was produced.

    PR_GATE went with the gate, which demanded a suite nothing could create and
    executed the model itself. TRACE_PROMOTION never had a reference outside
    this enum.
    """

    EVAL = "EVAL"
    NIGHTLY_LOO = "NIGHTLY_LOO"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResultStatus(StrEnum):
    COMPLETED = "completed"
    ERROR = "error"
    TIMEOUT = "timeout"


class VerdictOutcome(StrEnum):
    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"
    # Asked, and declined to answer. Not a failure: counting it as one hides
    # over-deferral, which is the difference between a cautious system and a
    # wrong one. Stays in the denominator, unlike ERROR.
    DEFER = "DEFER"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class Run(Base, IdMixin, TimestampsMixin):
    __tablename__ = "runs"

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="RESTRICT"), nullable=False
    )
    suite_id: Mapped[UUID] = mapped_column(
        ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalized benchmark name: eval items are keyed by it, and grading
    # matches on it. The FK above is the identity; this is the join key to gold.
    suite: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[HarnessMode] = mapped_column(String(40), nullable=False)
    pass_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_sweep_id: Mapped[UUID | None] = mapped_column(nullable=True)
    sweep_arm: Mapped[str | None] = mapped_column(String(100), nullable=True)
    config: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    # A configuration's identity, lifted out of `config` so it can be grouped,
    # filtered and indexed. `config_digest` decides which runs are the same
    # configuration; `config_label` is what the runner calls it.
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    config_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    config_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[RunStatus] = mapped_column(String(20), nullable=False, default=RunStatus.PENDING)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # A bad experiment is retired, not deleted: the row and its results stay so
    # the record is honest, and it leaves every aggregate. Distinct from
    # ``status``, which is how the execution ended -- a COMPLETED run is
    # exactly the kind that gets invalidated.
    invalidated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    invalidated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def is_valid(self) -> bool:
        """Whether this run still counts toward anything."""
        return self.invalidated_at is None

    __table_args__ = (
        UniqueConstraint(
            "suite_id",
            "solution_id",
            "dataset_version",
            "mode",
            "pass_idx",
            "parent_sweep_id",
            "sweep_arm",
            name="uq_run_suite_pass",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_run_team", "team_id"),
        Index("ix_run_status", "status"),
    )


class Result(Base, IdMixin, TimestampsMixin):
    __tablename__ = "results"

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[str] = mapped_column(String(200), nullable=False)
    attempt_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    output_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="json")
    # NULL means the writer had no measurement, which is not the same claim as
    # zero: both file importers push results whose report format carries no
    # per-item token count, and a 0 there reaches the matrix as a median of 0
    # and reads as a configuration that costs nothing.
    tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # Not nullable, and deliberately: every report format carries ``ms`` and
    # every runner path measures elapsed time, so there is no absence to say.
    runtime_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ResultStatus] = mapped_column(String(20), nullable=False)
    outcome: Mapped[VerdictOutcome | None] = mapped_column(String(20), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "item_id", "attempt_idx", name="uq_result_run_item_attempt"),
        Index("ix_result_team", "team_id"),
        Index("ix_result_run", "run_id"),
    )


class Verdict(Base, IdMixin, TimestampsMixin):
    __tablename__ = "verdicts"

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    result_id: Mapped[UUID] = mapped_column(
        ForeignKey("results.id", ondelete="CASCADE"), nullable=False
    )
    grader: Mapped[str] = mapped_column(String(100), nullable=False)
    grader_version: Mapped[str] = mapped_column(String(100), nullable=False)
    metric: Mapped[str | None] = mapped_column(String(100), nullable=True)
    criterion: Mapped[str] = mapped_column(String(100), nullable=False)
    bool_value: Mapped[bool | None] = mapped_column(nullable=True)
    value: Mapped[float | None] = mapped_column(nullable=True)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    canonical_answer: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    answer_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_verdict_result", "result_id"),
        Index("ix_verdict_team", "team_id"),
        Index("ix_verdict_answer_hash", "answer_hash"),
        Index("ix_verdict_metric", "metric"),
        CheckConstraint(
            "value IS NULL OR (value >= 0 AND value <= 1)",
            name="ck_verdict_value_range",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_verdict_confidence_range",
        ),
    )


class Trace(Base, IdMixin, TimestampsMixin):
    __tablename__ = "traces"

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    result_id: Mapped[UUID] = mapped_column(
        ForeignKey("results.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    step_tree: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    object_storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_trace_team", "team_id"),)
