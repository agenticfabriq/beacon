"""A runner registers itself by declaring what it is.

Beacon does not execute, so the runner is the only thing that knows its
identity, version and layers. Making someone retype them into a form guarantees
drift. The contract is the one result ingestion already uses: first declaration
creates, an identical one is a no-op, and a divergent one is refused -- because
a version's declared layers are what attribution ablates, so rewriting them
reinterprets every comparison already drawn against that version.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

import pytest
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from httpx2 import Response
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

SUITE = "declaration_v1"


class _World(Protocol):
    acme_team_id: UUID
    chat_to_data_id: UUID
    alice_id: UUID
    alice_key: str


@pytest.fixture
def suite_id(session: Session, world: _World) -> str:
    suite = SuiteRepo(session).create(
        project_id=world.chat_to_data_id,
        team_id=world.acme_team_id,
        name=SUITE,
        description="",
        method="manual",
        suite_metadata={},
        created_by=world.alice_id,
    )
    session.commit()
    return str(suite.id)


def _layers(*names: str) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": f"the {name} layer",
            "ablation_semantic": "skip",
            "instrumentation": "trace",
        }
        for name in names
    ]


def _declare(
    api_client: TestClient,
    world: _World,
    suite_id: str,
    *,
    version: str = "0.1.0",
    layers: list[dict[str, Any]] | None = None,
    solution_id: str = "declaring-sut",
) -> Response:
    return api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.alice_key},
        json={
            "solution": {
                "solution_id": solution_id,
                "version": version,
                "summary": "declared by its runner",
                "supported_modes": ["EVAL"],
                "layers": _layers("grounding", "verifier") if layers is None else layers,
            },
            "suite_id": suite_id,
            "mode": "EVAL",
            "config": {"model_id": "m", "layers_enabled": {}},
        },
    )


def test_a_first_declaration_registers_the_system_and_starts_the_run(
    api_client: TestClient, world: _World, suite_id: str, session: Session
) -> None:
    response = _declare(api_client, world, suite_id)

    assert response.status_code == 202, response.text
    assert response.json()["run_id"]
    registered = SolutionRepo(session).get_by_team_and_solution(
        world.acme_team_id, "declaring-sut", "0.1.0"
    )
    assert registered is not None
    assert {layer["name"] for layer in registered.layers} == {"grounding", "verifier"}


def test_an_identical_re_declaration_reuses_the_registration(
    api_client: TestClient, world: _World, suite_id: str, session: Session
) -> None:
    """A runner pushing its second run must not mint a second system."""
    first = _declare(api_client, world, suite_id)
    second = _declare(api_client, world, suite_id)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["solution_id"] == second.json()["solution_id"]
    assert len(SolutionRepo(session).list_for_team(world.acme_team_id)) >= 1


def test_two_runs_of_one_declaration_are_distinct_runs(
    api_client: TestClient, world: _World, suite_id: str
) -> None:
    first = _declare(api_client, world, suite_id)
    second = _declare(api_client, world, suite_id)

    assert first.json()["run_id"] != second.json()["run_id"]


def test_re_declaring_a_version_with_different_layers_is_refused(
    api_client: TestClient, world: _World, suite_id: str
) -> None:
    """The finding this exists for: a dropped layer would silently rewrite meaning."""
    _declare(api_client, world, suite_id)

    changed = _declare(api_client, world, suite_id, layers=_layers("grounding"))

    assert changed.status_code == 409
    detail = changed.json()["detail"]
    assert "verifier" in detail
    assert "publish a new version" in detail


def test_the_refusal_does_not_rewrite_the_registration(
    api_client: TestClient, world: _World, suite_id: str, session: Session
) -> None:
    _declare(api_client, world, suite_id)

    _declare(api_client, world, suite_id, layers=_layers("grounding"))

    registered = SolutionRepo(session).get_by_team_and_solution(
        world.acme_team_id, "declaring-sut", "0.1.0"
    )
    assert registered is not None
    assert {layer["name"] for layer in registered.layers} == {"grounding", "verifier"}


def test_a_new_version_may_declare_different_layers(
    api_client: TestClient, world: _World, suite_id: str
) -> None:
    """Dropping a layer is legitimate; it is a new version, not a rewrite."""
    _declare(api_client, world, suite_id)

    next_version = _declare(
        api_client, world, suite_id, version="0.2.0", layers=_layers("grounding")
    )

    assert next_version.status_code == 202


def test_a_declared_system_is_attached_to_the_project(
    api_client: TestClient, world: _World, suite_id: str
) -> None:
    """A runner that declares itself has no separate step in which to be attached."""
    declared = _declare(api_client, world, suite_id)

    attached = api_client.get(
        f"/v1/projects/{world.chat_to_data_id}/solutions",
        headers={"X-API-Key": world.alice_key},
    ).json()

    assert declared.json()["solution_id"] in [str(row["solution_id"]) for row in attached]


def test_a_catalogued_solution_can_still_be_named_by_id(
    api_client: TestClient, world: _World, suite_id: str
) -> None:
    """The existing path keeps working: declaring is an addition, not a swap."""
    declared = _declare(api_client, world, suite_id)
    solution_id = declared.json()["solution_id"]

    by_id = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.alice_key},
        json={
            "solution_id": solution_id,
            "suite_id": suite_id,
            "mode": "EVAL",
            "config": {},
        },
    )

    assert by_id.status_code == 202, by_id.text


def test_naming_a_solution_both_ways_is_refused(
    api_client: TestClient, world: _World, suite_id: str
) -> None:
    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.alice_key},
        json={
            "solution_id": str(uuid4()),
            "solution": {"solution_id": "s", "version": "1"},
            "suite_id": suite_id,
            "mode": "EVAL",
            "config": {},
        },
    )

    assert response.status_code == 422


def test_naming_a_solution_neither_way_is_refused(
    api_client: TestClient, world: _World, suite_id: str
) -> None:
    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.alice_key},
        json={"suite_id": suite_id, "mode": "EVAL", "config": {}},
    )

    assert response.status_code == 422


def test_a_declaration_with_no_layers_is_allowed(
    api_client: TestClient, world: _World, suite_id: str
) -> None:
    """Not every system is ablatable; it just cannot be attributed."""
    response = _declare(api_client, world, suite_id, solution_id="flat-sut", layers=[])

    assert response.status_code == 202
