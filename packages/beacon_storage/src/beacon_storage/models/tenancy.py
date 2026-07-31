"""User, Team, Project, Membership, ApiKey -- the multi-tenant root."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import StrEnum
from uuid import UUID  # noqa: TC003

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, IdMixin, TimestampsMixin


class Role(StrEnum):
    BEACON_ADMIN = "beacon_admin"
    VIEWER = "viewer"
    TEAM_ADMIN = "team_admin"
    TEAM_MEMBER = "team_member"
    PROJECT_OWNER = "project_owner"
    PROJECT_CONTRIBUTOR = "project_contributor"
    PROJECT_VIEWER = "project_viewer"


class ScopeKind(StrEnum):
    GLOBAL = "global"
    TEAM = "team"
    PROJECT = "project"


class User(Base, IdMixin, TimestampsMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class Team(Base, IdMixin, TimestampsMixin):
    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Project(Base, IdMixin, TimestampsMixin):
    __tablename__ = "projects"

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_run_id: Mapped[UUID | None] = mapped_column(nullable=True)  # FK added in P2
    gate_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("team_id", "name", name="uq_project_team_name"),
        Index("ix_project_team_active", "team_id", "archived_at"),
    )


class Membership(Base, TimestampsMixin):
    """User x (team|project) x role. One role per user and scope."""

    __tablename__ = "memberships"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    scope_kind: Mapped[ScopeKind] = mapped_column(String(20), primary_key=True)
    scope_id: Mapped[UUID] = mapped_column(primary_key=True)
    role: Mapped[Role] = mapped_column(String(40), nullable=False)
    granted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "scope_kind IN ('global','team','project')",
            name="ck_membership_scope_kind",
        ),
        Index("ix_membership_scope", "scope_kind", "scope_id"),
    )


class ApiKey(Base, IdMixin, TimestampsMixin):
    """Personal API keys. Carries the user's membership set at request time."""

    __tablename__ = "api_keys"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
