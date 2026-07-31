"""Anti-Goodhart auditor findings."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import StrEnum
from typing import Any
from uuid import UUID  # noqa: TC003

from sqlalchemy import TIMESTAMP, CheckConstraint, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, IdMixin


class AntigoodhartKind(StrEnum):
    SQL_IN_QUESTION = "sql_in_question"
    EVIDENCE_LEAK = "evidence_leak"
    METADATA_BLEED = "metadata_bleed"
    DISTRIBUTION_SKEW = "distribution_skew"


class AntigoodhartSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AntigoodhartFinding(IdMixin, Base):
    """Append-only finding emitted by an anti-Goodhart scan."""

    __tablename__ = "antigoodhart_findings"

    scan_id: Mapped[UUID] = mapped_column(nullable=False)
    item_id: Mapped[UUID | None] = mapped_column(nullable=True)
    team_id: Mapped[UUID | None] = mapped_column(nullable=True)
    suite_id: Mapped[UUID | None] = mapped_column(nullable=True)

    kind: Mapped[AntigoodhartKind] = mapped_column(String(40), nullable=False)
    severity: Mapped[AntigoodhartSeverity] = mapped_column(String(10), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('sql_in_question','evidence_leak','metadata_bleed','distribution_skew')",
            name="ck_antigoodhart_kind",
        ),
        CheckConstraint(
            "severity IN ('low','medium','high')",
            name="ck_antigoodhart_severity",
        ),
        Index("ix_antigoodhart_scan", "scan_id"),
        Index("ix_antigoodhart_item", "item_id"),
        Index("ix_antigoodhart_team_suite", "team_id", "suite_id"),
        Index("ix_antigoodhart_kind_severity", "kind", "severity"),
    )
