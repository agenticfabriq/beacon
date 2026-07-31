from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from beacon_storage.models.tenancy import Membership, Role, ScopeKind

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class MembershipRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def grant(
        self,
        *,
        user_id: UUID,
        scope_kind: ScopeKind,
        scope_id: UUID,
        role: Role,
        granted_by: UUID | None = None,
    ) -> Membership:
        """Grant ``role`` to ``user_id`` on the given scope, upserting on conflict."""
        m = Membership(
            user_id=user_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            role=role,
            granted_by=granted_by,
        )
        merged = self.session.merge(m)
        self.session.flush()
        return merged

    def revoke(self, *, user_id: UUID, scope_kind: ScopeKind, scope_id: UUID) -> None:
        """Remove the membership of ``user_id`` on the given scope."""
        self.session.execute(
            delete(Membership).where(
                Membership.user_id == user_id,
                Membership.scope_kind == scope_kind,
                Membership.scope_id == scope_id,
            )
        )

    def list_for_user(self, user_id: UUID) -> list[Membership]:
        """Return every membership held by ``user_id``."""
        return list(self.session.scalars(select(Membership).where(Membership.user_id == user_id)))

    def list_for_scope(self, scope_kind: ScopeKind, scope_id: UUID) -> list[Membership]:
        """Return every membership recorded on the given scope."""
        return list(
            self.session.scalars(
                select(Membership).where(
                    Membership.scope_kind == scope_kind,
                    Membership.scope_id == scope_id,
                )
            )
        )
