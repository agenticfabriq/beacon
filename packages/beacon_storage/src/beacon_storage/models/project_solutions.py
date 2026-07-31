from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from sqlalchemy import TIMESTAMP, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base


class ProjectSolution(Base):
    """Project to team-catalog solution link."""

    __tablename__ = "project_solutions"

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_project_solution_team", "team_id"),
        Index("ix_project_solution_project", "project_id"),
        Index("ix_project_solution_solution", "solution_id"),
    )
