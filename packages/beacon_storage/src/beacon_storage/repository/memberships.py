from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select, text

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

    def may_issue_key_for(self, *, issuer_id: UUID, target_id: UUID) -> bool:
        """Whether ``issuer_id`` may mint a credential authenticating as ``target_id``.

        Delegates to the SQL function of the same name rather than comparing
        memberships here, for one reason: this repository's own reads are
        bounded by ``memberships_read``, so a membership the target holds in a
        scope the issuer is not in would be INVISIBLE to a Python-side check.
        It would then find nothing outside the issuer's scopes and permit --
        a guard whose pass value means "I could not look". The function is
        SECURITY DEFINER and sees the whole table.

        It is also the predicate in ``api_keys_insert``, so the route's answer
        and the database's cannot drift apart.
        """
        return bool(
            self.session.execute(
                text("SELECT may_issue_key_for(:issuer, :target)"),
                {"issuer": str(issuer_id), "target": str(target_id)},
            ).scalar_one()
        )
