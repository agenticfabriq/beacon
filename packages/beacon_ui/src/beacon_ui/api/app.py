"""FastAPI application factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_iam.errors import BeaconIamError
from beacon_registry.errors import BeaconRegistryError
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from beacon_ui.api.errors import beacon_error_handler
from beacon_ui.api.openapi import install_openapi_role_metadata

if TYPE_CHECKING:
    from fastapi import Request, Response


async def _beacon_exception_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, BeaconIamError):
        raise exc
    return await beacon_error_handler(request, exc)


async def _registry_exception_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, BeaconRegistryError):
        raise exc
    return JSONResponse(
        status_code=400,
        content={"code": exc.code, "message": str(exc)},
    )


def create_app() -> FastAPI:
    """Build the Beacon FastAPI application with routes and error handlers."""
    app = FastAPI(title="Beacon API", version="0.2.0")
    app.add_exception_handler(BeaconIamError, _beacon_exception_handler)
    app.add_exception_handler(BeaconRegistryError, _registry_exception_handler)
    from beacon_ui.api.routes import register_routes

    register_routes(app)
    install_openapi_role_metadata(app)
    return app


app = create_app()
