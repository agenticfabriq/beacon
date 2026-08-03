"""Schemas for the caller's own API keys."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=100)


class ApiKeyCreatedOut(BaseModel):
    """The only response that ever carries the key itself.

    Beacon stores a hash, so this value cannot be recovered afterwards; the
    listing deliberately has no field for it.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    label: str
    api_key: str
    created_at: datetime


class ApiKeyOut(BaseModel):
    """A key as it can be shown after creation: enough to decide what to revoke."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    label: str
    created_at: datetime
    last_used_at: datetime | None = None


class ApiKeyListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[ApiKeyOut]
