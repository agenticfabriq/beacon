from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, EmailStr


class MembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scope_kind: str
    scope_id: UUID
    role: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    name: str


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
