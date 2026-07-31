from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.models.tenancy import Team

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class TeamRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, name: str, description: str | None = None) -> Team:
        """Persist a new team and return it."""
        t = Team(name=name, description=description)
        self.session.add(t)
        self.session.flush()
        return t

    def get(self, team_id: UUID) -> Team | None:
        """Return the team with id ``team_id`` or None."""
        return self.session.get(Team, team_id)

    def get_by_name(self, name: str) -> Team | None:
        """Return the team whose name equals ``name`` or None."""
        return self.session.scalar(select(Team).where(Team.name == name))

    def list(self) -> list[Team]:
        """Return all teams ordered by name."""
        return list(self.session.scalars(select(Team).order_by(Team.name)))
