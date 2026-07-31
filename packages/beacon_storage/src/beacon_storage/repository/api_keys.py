from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.models.tenancy import ApiKey

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class ApiKeyRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, user_id: UUID, key_hash: str, label: str) -> ApiKey:
        """Persist a new API key owned by ``user_id`` and return it."""
        k = ApiKey(user_id=user_id, key_hash=key_hash, label=label)
        self.session.add(k)
        self.session.flush()
        return k

    def get_by_hash(self, key_hash: str) -> ApiKey | None:
        """Return the active (non-revoked) API key matching ``key_hash``."""
        return self.session.scalar(
            select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
        )

    def touch(self, api_key_id: UUID) -> None:
        """Update ``last_used_at`` on the API key to the current time."""
        k = self.session.get(ApiKey, api_key_id)
        if k is not None:
            k.last_used_at = datetime.now(UTC)
            self.session.flush()

    def revoke(self, api_key_id: UUID) -> None:
        """Mark the API key revoked by setting ``revoked_at`` to the current time."""
        k = self.session.get(ApiKey, api_key_id)
        if k is not None:
            k.revoked_at = datetime.now(UTC)
            self.session.flush()
