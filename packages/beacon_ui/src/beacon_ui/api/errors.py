"""Map Beacon IAM errors to HTTP responses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_iam.errors import (
    AuthenticationError,
    AuthorizationError,
    BeaconIamError,
    ConflictError,
    NotFoundError,
)
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from fastapi import Request

_STATUS_BY_CODE = {
    AuthenticationError: 401,
    AuthorizationError: 403,
    NotFoundError: 404,
    ConflictError: 409,
}


async def beacon_error_handler(request: Request, exc: BeaconIamError) -> JSONResponse:
    """Translate a BeaconIamError into a JSON response with the matching status."""
    status_code = _STATUS_BY_CODE.get(type(exc), 400)
    return JSONResponse(
        status_code=status_code,
        content={"code": exc.code, "message": str(exc)},
    )
