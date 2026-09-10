from typing import Protocol
from uuid import UUID

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.suites import Suite
from beacon_storage.repository.eval_items import EvalItemRepo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    alice_id: UUID
    alice_key: str
    carol_key: str


def _create_item(session: Session, world: _World, question: str) -> UUID:
    item = EvalItemRepo(session).create(
        tier=EvalItemTier.EXECUTION_CONFIRMED,
        suite="bird_minidev_v2",
        team_id=world.acme_team_id,
        item_input={"question": question},
        gold_answer={"sql": "SELECT 1"},
        item_metadata={"source": "task-7-test"},
        created_by=world.alice_id,
    )
    return item.item_id


def test_create_manual_suite(api_client: TestClient, world: _World, session: Session) -> None:
    item_ids = [
        _create_item(session, world, "Q1"),
        _create_item(session, world, "Q2"),
    ]
    session.commit()

    response = api_client.post(
        f"/v1/teams/{world.acme_team_id}/suites",
        headers={"X-API-Key": world.alice_key},
        json={
            "name": "smoke-10",
            "kind": "manual",
            "item_ids": [str(item_id) for item_id in item_ids],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "smoke-10"
    assert body["item_count"] == 2


def test_create_curated_suite(api_client: TestClient, world: _World) -> None:
    response = api_client.post(
        f"/v1/teams/{world.acme_team_id}/suites",
        headers={"X-API-Key": world.alice_key},
        json={
            "name": "regression-50",
            "kind": "curated",
            "source_suite": "bird_minidev_v2",
            "target_size": 50,
        },
    )

    assert response.status_code in (200, 201, 400), response.text


# A non-member gets a different status depending on the connection role, and
# both are correct for their role. `require_permission` resolves the scope with
# `TeamRepo.get` first and raises 404 when it comes back None; as the owning
# role nothing filters that read, so the caller reaches the permission check and
# gets 403, while under `beacon_app` the team row is invisible to someone with
# no membership in it and the 404 fires first.
#
# 404 is the answer we want: 403 confirms the team is real to someone with no
# business knowing, so walking a list of ids would reveal which exist. It costs
# a legitimate user nothing -- a member who merely lacks the permission can
# still SEE the team, so they reach the check and still get 403. Measured both
# ways; only a true outsider falls to 404.
#
# Parametrized rather than loosened to "either", so each role's answer is
# pinned and a change to either is a failure. `owner_client` and not
# `api_client`, because `api_client` follows BEACON_TEST_CONSTRAINED and the
# two parameters would collapse into one under the flag.
@pytest.mark.parametrize(
    ("which", "expected"), [("owner_client", 403), ("constrained_client", 404)]
)
def test_outsider_cannot_create_suite(
    which: str, expected: int, request: pytest.FixtureRequest, world: _World
) -> None:
    client: TestClient = request.getfixturevalue(which)
    response = client.post(
        f"/v1/teams/{world.acme_team_id}/suites",
        headers={"X-API-Key": world.carol_key},
        json={"name": "x", "kind": "manual", "item_ids": []},
    )

    assert response.status_code == expected, response.text


def test_list_suites(api_client: TestClient, world: _World) -> None:
    create_response = api_client.post(
        f"/v1/teams/{world.acme_team_id}/suites",
        headers={"X-API-Key": world.alice_key},
        json={"name": "smoke-list", "kind": "manual", "item_ids": []},
    )
    assert create_response.status_code == 201, create_response.text

    response = api_client.get(
        f"/v1/teams/{world.acme_team_id}/suites",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 200, response.text
    names = [suite["name"] for suite in response.json()]
    assert "smoke-list" in names


def _seed_run(session: Session, world: _World, suite: Suite, *, invalid: bool = False) -> None:
    """Register one run against `suite`, optionally already invalidated."""
    from datetime import UTC, datetime

    from beacon_storage.models.runs import Run, RunStatus
    from beacon_storage.repository.solutions import SolutionRepo

    solution = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id=f"run-count-sut-{'inv' if invalid else 'ok'}",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=world.alice_id,
    )
    extra = (
        {
            "invalidated_at": datetime.now(UTC),
            "invalidated_by": world.alice_id,
            "invalidation_reason": "seeded invalid",
        }
        if invalid
        else {}
    )
    session.add(
        Run(
            team_id=world.acme_team_id,
            suite_id=suite.id,
            suite=suite.name,
            dataset_version="test-1",
            solution_id=solution.id,
            mode="EVAL",
            status=RunStatus.CANCELLED if invalid else RunStatus.COMPLETED,
            config={},
            created_by=world.alice_id,
            **extra,
        )
    )
    session.commit()


def _run_count(api_client: TestClient, world: _World, suite_id: UUID) -> int:
    body = api_client.get(
        f"/v1/teams/{world.acme_team_id}/suites", headers={"X-API-Key": world.alice_key}
    ).json()
    return int(next(s for s in body if s["id"] == str(suite_id))["run_count"])


def test_the_suites_response_carries_a_run_count(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """The count the benchmarks view needs, on the response it already fetches.

    That view used to fire one request PER SUITE, asking for up to 500 whole
    run objects only to take `.length` -- N+1 in the number of benchmarks, and
    a 288-run payload on bird_minidev_v2 to render a single number. Reported as
    slow from use, which is how it was found.
    """
    from beacon_storage.models.suites import Suite
    from sqlalchemy import select

    suite = session.scalars(select(Suite).where(Suite.team_id == world.acme_team_id)).first()
    assert suite is not None
    baseline = _run_count(api_client, world, suite.id)

    _seed_run(session, world, suite)

    assert _run_count(api_client, world, suite.id) == baseline + 1


def test_the_run_count_includes_invalidated_runs(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """Deliberately the same population the client counted, not a better one.

    The old client passed `include_invalidated: true`, so bird_minidev_v2 reads
    288 here while the results matrix aggregates 42 valid runs. Serving a
    different number from the same column would have changed a figure on screen
    as a side effect of making it cheaper. That gap is real and worth surfacing
    one day; it was not this change's to decide.
    """
    from beacon_storage.models.suites import Suite
    from sqlalchemy import select

    suite = session.scalars(select(Suite).where(Suite.team_id == world.acme_team_id)).first()
    assert suite is not None
    baseline = _run_count(api_client, world, suite.id)

    _seed_run(session, world, suite, invalid=True)

    assert _run_count(api_client, world, suite.id) == baseline + 1
