"""User, Team, Membership, ApiKey -- the multi-tenant root."""

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
)
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, IdMixin, TimestampsMixin


class Role(StrEnum):
    BEACON_ADMIN = "beacon_admin"
    VIEWER = "viewer"
    TEAM_ADMIN = "team_admin"
    TEAM_MEMBER = "team_member"


class ScopeKind(StrEnum):
    GLOBAL = "global"
    TEAM = "team"


class User(Base, IdMixin, TimestampsMixin):
    __tablename__ = "users"
    # Same reason as ``ApiKey``: RETURNING on an insert is read back through the
    # SELECT policy, and ``users_read`` cannot see a user nobody is yet a
    # co-member of. Measured as the serving role, inserting an invitee -- the
    # plain INSERT was ACCEPTED and the same INSERT with RETURNING was REFUSED,
    # so inviting a NEW person failed while inviting an existing one worked.
    # That asymmetry is why the existing suite missed it: its invite test uses
    # an account that already exists.
    __mapper_args__ = {"eager_defaults": False}

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class Team(Base, IdMixin, TimestampsMixin):
    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    # No RETURNING on insert, and this is a SECURITY property rather than a
    # performance one.
    #
    # `TimestampsMixin` gives both timestamps a server default, so by default
    # SQLAlchemy flushes `INSERT ... RETURNING created_at, updated_at` to read
    # them back. Postgres applies SELECT policies to a RETURNING row -- so with
    # `api_keys_read` restricted to the key's OWNER, which is deliberate and
    # what `test_api_key_hashes_stay_private_to_their_owner` protects, the
    # RETURNING made `issue_member_key` fail for every admin under the
    # constrained role. Measured: the same insert was ACCEPTED plain and
    # REFUSED with RETURNING.
    #
    # The alternative was widening read to admins, which trades away a real
    # invariant to work around an ORM detail. This keeps both: the insert needs
    # no read, and a caller that wants the timestamps still gets them, because
    # `eager_defaults=False` leaves those two columns UNLOADED after the flush
    # rather than filled in -- so `create_api_key` reading `record.created_at`
    # lazy-loads them on first access. That is a SELECT the key's own owner is
    # allowed. (Not because the session expires on commit: `make_session_factory`
    # sets `expire_on_commit=False`. The reload happens because the attribute
    # was never populated, which is a different mechanism reaching the same
    # place, and worth stating correctly -- a maintainer who believed the
    # expiry story could remove this line and conclude nothing would change.)
    __mapper_args__ = {"eager_defaults": False}

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
