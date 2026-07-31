from typing import Protocol

import pytest
from beacon_iam.permissions import Permission
from beacon_ui.api.deps import require_permission
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _World(Protocol):
    chat_to_data_id: object
    alice_key: str
    carol_key: str


def _make_app(permission: Permission) -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    @router.get("/check/{project_id}")
    def check(
        _user: object = Depends(require_permission(permission, scope_kind="project")),
    ) -> dict[str, bool]:
        return {"ok": True}

    app.include_router(router)
    return app


def test_owner_can_access(world: _World, monkeypatch: pytest.MonkeyPatch, db_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    import beacon_ui.api.deps as deps_mod

    deps_mod._factory = None
    app = _make_app(Permission.PROJECT_MANAGE)
    client = TestClient(app)

    response = client.get(
        f"/check/{world.chat_to_data_id}",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 200


def test_non_member_denied(world: _World, monkeypatch: pytest.MonkeyPatch, db_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    import beacon_ui.api.deps as deps_mod

    deps_mod._factory = None
    app = _make_app(Permission.PROJECT_MANAGE)
    client = TestClient(app)

    response = client.get(
        f"/check/{world.chat_to_data_id}",
        headers={"X-API-Key": world.carol_key},
    )

    assert response.status_code == 403


def test_unauthenticated_returns_401(
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    db_url: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    import beacon_ui.api.deps as deps_mod

    deps_mod._factory = None
    app = _make_app(Permission.PROJECT_MANAGE)
    client = TestClient(app)

    response = client.get(f"/check/{world.chat_to_data_id}")

    assert response.status_code == 401
