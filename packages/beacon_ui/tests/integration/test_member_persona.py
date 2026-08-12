"""The UI's boot sequence, replayed as each persona.

Every call the web UI makes on sign-in and first render, in order, as a
plain team_member and as the admin -- so a change that breaks one persona's
workspace fails here before a person finds it. Regression test for the day
the teams listing leaked a foreign team, the UI defaulted into it, and the
resulting 403 signed the member out on every refresh.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID  # noqa: TC003

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    globex_team_id: UUID
    acme_suite_id: UUID
    alice_key: str
    bob_key: str


def _boot(api_client: TestClient, key: str) -> dict[str, Any]:
    """Replay the UI's sign-in sequence; every step must succeed."""
    headers = {"X-API-Key": key}
    me = api_client.get("/v1/me", headers=headers)
    assert me.status_code == 200, me.text
    teams = api_client.get("/v1/teams", headers=headers)
    assert teams.status_code == 200, teams.text
    team_rows = teams.json()
    memberships = me.json()["memberships"]
    mine = {m["scope_id"] for m in memberships if m["scope_kind"] == "team"}
    preferred = next((t for t in team_rows if t["id"] in mine), team_rows[0] if team_rows else None)
    assert preferred is not None, "no team to land in"
    suites = api_client.get(f"/v1/teams/{preferred['id']}/suites", headers=headers)
    assert suites.status_code == 200, suites.text
    return {"teams": team_rows, "team": preferred, "suites": suites.json()}


def test_a_member_boots_into_their_own_team_and_only_sees_it(
    api_client: TestClient, world: _World
) -> None:
    boot = _boot(api_client, world.bob_key)

    team_ids = {t["id"] for t in boot["teams"]}
    assert str(world.acme_team_id) in team_ids
    assert str(world.globex_team_id) not in team_ids, "a member must not see foreign teams"
    assert boot["team"]["id"] == str(world.acme_team_id)

    suite_ids = {s["suite_id"] for s in boot["suites"]}
    assert str(world.acme_suite_id) in suite_ids
    matrix = api_client.get(
        f"/v1/suites/{world.acme_suite_id}/results-matrix",
        headers={"X-API-Key": world.bob_key},
    )
    assert matrix.status_code == 200, matrix.text


def test_a_member_boot_is_stable_across_refresh(api_client: TestClient, world: _World) -> None:
    """The exact repro: refresh replays the boot; nothing may 401 or 403."""
    first = _boot(api_client, world.bob_key)
    second = _boot(api_client, world.bob_key)

    assert first["team"]["id"] == second["team"]["id"]


def test_the_admin_sees_all_teams_but_lands_in_their_own(
    api_client: TestClient, world: _World
) -> None:
    boot = _boot(api_client, world.alice_key)

    team_ids = {t["id"] for t in boot["teams"]}
    assert {str(world.acme_team_id), str(world.globex_team_id)} <= team_ids
    assert boot["team"]["id"] == str(world.acme_team_id)


def test_an_empty_team_can_be_deleted_by_the_admin(api_client: TestClient, world: _World) -> None:
    created = api_client.post(
        "/v1/teams",
        json={"name": "typo-team", "description": ""},
        headers={"X-API-Key": world.alice_key},
    )
    assert created.status_code == 201, created.text

    deleted = api_client.delete(
        f"/v1/teams/{created.json()['id']}",
        headers={"X-API-Key": world.alice_key},
    )
    assert deleted.status_code == 204, deleted.text


def test_a_creator_is_on_the_roster_as_team_admin(api_client: TestClient, world: _World) -> None:
    """A team is never born ownerless."""
    created = api_client.post(
        "/v1/teams",
        json={"name": "owned-team", "description": ""},
        headers={"X-API-Key": world.alice_key},
    )
    assert created.status_code == 201, created.text

    roster = api_client.get(
        f"/v1/teams/{created.json()['id']}/members",
        headers={"X-API-Key": world.alice_key},
    ).json()
    members = roster if isinstance(roster, list) else roster["members"]
    assert [(m["email"], m["role"]) for m in members] == [("alice@example.com", "team_admin")]


def test_deleting_a_team_revokes_its_grants(api_client: TestClient, world: _World) -> None:
    """Grants are access, not data: they go with the team."""
    created = api_client.post(
        "/v1/teams",
        json={"name": "short-lived", "description": ""},
        headers={"X-API-Key": world.alice_key},
    )
    team_id = created.json()["id"]
    added = api_client.post(
        f"/v1/teams/{team_id}/members",
        headers={"X-API-Key": world.alice_key},
        json={"user_email": "bob@example.com", "role": "team_member"},
    )
    assert added.status_code == 201, added.text

    deleted = api_client.delete(f"/v1/teams/{team_id}", headers={"X-API-Key": world.alice_key})
    assert deleted.status_code == 204, deleted.text

    bob_teams = api_client.get("/v1/teams", headers={"X-API-Key": world.bob_key}).json()
    assert team_id not in {t["id"] for t in bob_teams}


def test_a_team_holding_anything_refuses_deletion(api_client: TestClient, world: _World) -> None:
    response = api_client.delete(
        f"/v1/teams/{world.acme_team_id}",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 409, response.text
    assert "holds data" in response.json()["detail"]


def test_a_member_cannot_delete_a_team(api_client: TestClient, world: _World) -> None:
    response = api_client.delete(
        f"/v1/teams/{world.acme_team_id}",
        headers={"X-API-Key": world.bob_key},
    )

    assert response.status_code == 403
