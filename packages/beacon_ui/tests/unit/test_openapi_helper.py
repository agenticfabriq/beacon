from beacon_iam.permissions import Permission
from beacon_ui.api.openapi import (
    extract_required_permissions,
    install_openapi_role_metadata,
    requires,
)
from fastapi import FastAPI


def test_requires_decorator_attaches_metadata() -> None:
    @requires(Permission.PROJECT_MANAGE)
    def handler() -> None:
        pass

    required = vars(handler)["_beacon_required_permissions"]
    assert required == (Permission.PROJECT_MANAGE,)


def test_requires_supports_multiple_permissions() -> None:
    @requires(Permission.PROJECT_VIEW, Permission.PROJECT_RUN_EVAL)
    def handler() -> None:
        pass

    required = vars(handler)["_beacon_required_permissions"]
    assert required == (
        Permission.PROJECT_VIEW,
        Permission.PROJECT_RUN_EVAL,
    )


def test_install_adds_x_required_permissions_to_schema() -> None:
    app = FastAPI()

    @app.get("/x")
    @requires(Permission.TEAM_MANAGE)
    def x_handler() -> dict[str, str]:
        return {"ok": "yes"}

    install_openapi_role_metadata(app)
    schema = app.openapi()
    operation = schema["paths"]["/x"]["get"]
    assert "x-required-permissions" in operation
    assert "team.manage" in operation["x-required-permissions"]


def test_extract_required_permissions_helper_returns_set() -> None:
    app = FastAPI()

    @app.get("/y")
    @requires(Permission.PROJECT_VIEW)
    def y_handler() -> dict[str, str]:
        return {"ok": "yes"}

    permissions = extract_required_permissions(app, "/y", "get")
    assert permissions == {Permission.PROJECT_VIEW}
