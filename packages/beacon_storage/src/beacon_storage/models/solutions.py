"""Solution catalog entry.

A Solution is a team-owned SUT registration: it pins the SUT's identity
(solution_id + version), its supported modes, and the layers it declared at
registration time. Projects reference solutions via project_solutions (N:N,
added in P5). RLS visibility is filtered on team_id.
"""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, IdMixin, TimestampsMixin


class Solution(Base, IdMixin, TimestampsMixin):
    """Team-scoped SUT catalog entry.

    Note on naming: ``Solution.id`` is the row UUID v7 (PK).
    ``Solution.solution_id`` is the human-readable slug (e.g., "chat-to-data",
    "dummy"). We use ``solution_record_id`` in method signatures (e.g.,
    ``HarnessRunner.run_single``) when we mean the UUID PK, to avoid
    overloading the term ``solution_id``.
    """

    __tablename__ = "solutions"

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    solution_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_team: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    supported_modes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    layers: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("team_id", "solution_id", "version", name="uq_solution_team_id_version"),
        Index("ix_solution_team", "team_id"),
    )
