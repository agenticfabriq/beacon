"""Role-annotation helper for OpenAPI."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar, cast

from beacon_iam.permissions import Permission

if TYPE_CHECKING:
    from fastapi import FastAPI

F = TypeVar("F", bound=Callable[..., Any])


def requires(*permissions: Permission) -> Callable[[F], F]:
    """Mark a route handler as requiring the given permissions."""

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        wrapper._beacon_required_permissions = permissions  # type: ignore[attr-defined]
        func._beacon_required_permissions = permissions  # type: ignore[attr-defined]
        return cast("F", wrapper)

    return decorator


def install_openapi_role_metadata(app: FastAPI) -> None:
    """Patch ``app.openapi`` to include ``x-required-permissions`` operations."""
    if getattr(app, "_beacon_openapi_patched", False):
        return

    original_openapi = app.openapi

    def patched_openapi() -> dict[str, Any]:
        schema = original_openapi()
        paths = schema.get("paths", {})
        for route in app.routes:
            endpoint = getattr(route, "endpoint", None)
            permissions = getattr(endpoint, "_beacon_required_permissions", None)
            if not permissions:
                continue

            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if path is None or methods is None:
                continue

            for method in methods:
                operation = paths.get(path, {}).get(str(method).lower())
                if operation is not None:
                    operation["x-required-permissions"] = [
                        permission.value for permission in permissions
                    ]
        return schema

    app.openapi = patched_openapi  # type: ignore[method-assign]
    app._beacon_openapi_patched = True  # type: ignore[attr-defined]


def extract_required_permissions(app: FastAPI, path: str, method: str) -> set[Permission]:
    """Return permissions declared in a route's OpenAPI operation metadata."""
    install_openapi_role_metadata(app)
    operation = app.openapi().get("paths", {}).get(path, {}).get(method.lower(), {})
    return {Permission(raw) for raw in operation.get("x-required-permissions", [])}
