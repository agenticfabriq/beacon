"""The caller's own API keys: create, list, revoke.

A key that can be minted and never retired is a credential with no expiry and
no owner-visible existence. The revocation machinery was already written and
tested at the storage layer; these are the routes that let anyone reach it.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from beacon_iam.auth.api_key import generate_api_key, hash_api_key
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.api_keys import ApiKeyRepo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.config import ApiConfig
from beacon_ui.api.deps import get_current_user, get_session
from beacon_ui.api.schemas.api_key import (
    ApiKeyCreatedOut,
    ApiKeyCreateIn,
    ApiKeyListOut,
    ApiKeyOut,
)

router = APIRouter(prefix="/v1/me/api-keys", tags=["api-keys"])


@router.post(
    "",
    response_model=ApiKeyCreatedOut,
    status_code=status.HTTP_201_CREATED,
    description="Required roles: authenticated user.",
    openapi_extra={"x-required-roles": ["authenticated"]},
)
def create_api_key(
    body: ApiKeyCreateIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> ApiKeyCreatedOut:
    """Mint a key for the caller and return it once."""
    key = generate_api_key(prefix=ApiConfig().api_key_prefix)
    record = ApiKeyRepo(session).create(
        user_id=user.id, key_hash=hash_api_key(key), label=body.label
    )
    session.commit()
    return ApiKeyCreatedOut(
        id=record.id, label=record.label, api_key=key, created_at=record.created_at
    )


@router.get(
    "",
    response_model=ApiKeyListOut,
    description="Required roles: authenticated user.",
    openapi_extra={"x-required-roles": ["authenticated"]},
)
def list_api_keys(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> ApiKeyListOut:
    """List the caller's active keys. Never returns key material."""
    records = ApiKeyRepo(session).list_for_user(user.id)
    return ApiKeyListOut(keys=[ApiKeyOut.model_validate(record) for record in records])


@router.delete(
    "/{api_key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Required roles: authenticated user.",
    openapi_extra={"x-required-roles": ["authenticated"]},
)
def revoke_api_key(
    api_key_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    """Revoke one of the caller's keys.

    A key belonging to someone else is reported as absent rather than refused,
    so this cannot be used to discover which key ids exist.
    """
    if not ApiKeyRepo(session).revoke_for_user(api_key_id, user_id=user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "api key not found")
    session.commit()
