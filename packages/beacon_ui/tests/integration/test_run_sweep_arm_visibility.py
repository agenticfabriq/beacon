"""The ablation arm is readable above the storage layer (B10).

`sweep_arm` was persisted by the B2 fix but nothing consumed it, so "which
arm produced this run" was recoverable only by diffing config.layers_enabled
against a baseline you had to identify separately.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID  # noqa: TC003

import pytest
from beacon_storage.ids import uuid7
from beacon_storage.models.runs import HarnessMode
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

SUITE = "arm_visibility_v1"
_ARMS = ("baseline", "no_ontology", "no_retry_loop")


class _World(Protocol):
    acme_team_id: UUID
    acme_suite_id: UUID
    alice_id: UUID
    alice_key: str


def _seed_sweep(session: Session, world: _World) -> UUID:
    """Persist one NIGHTLY_LOO sweep: three arms under a single sweep id."""
    solution = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="arm-sut",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="",
        supported_modes=["NIGHTLY_LOO"],
        layers=[],
        created_by=world.alice_id,
    )
    sweep_id = uuid7()
    repo = RunRepo(session)
    for arm in _ARMS:
        run = repo.create(
            team_id=world.acme_team_id,
            suite_id=world.acme_suite_id,
            solution_id=solution.id,
            suite=SUITE,
            dataset_version="v0",
            mode=HarnessMode.NIGHTLY_LOO,
            pass_idx=0,
            parent_sweep_id=sweep_id,
            sweep_arm=arm,
            config={},
            created_by=world.alice_id,
        )
        repo.mark_completed(run.id)
    session.commit()
    return sweep_id


def _list_runs(api_client: TestClient, world: _World, **params: str) -> list[dict[str, object]]:
    response = api_client.get(
        f"/v1/suites/{world.acme_suite_id}/runs",
        headers={"X-API-Key": world.alice_key},
        params=params,
    )
    assert response.status_code == 200, response.text
    return [dict(row) for row in response.json()]


def test_the_api_reports_which_arm_a_run_is(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    sweep_id = _seed_sweep(session, world)

    runs = _list_runs(api_client, world, parent_sweep_id=str(sweep_id))

    assert {run["sweep_arm"] for run in runs} == set(_ARMS)
    assert {run["parent_sweep_id"] for run in runs} == {str(sweep_id)}


def test_runs_can_be_filtered_to_one_arm(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    """Every pass of an arm is now reachable, not just the two Attribution pins."""
    sweep_id = _seed_sweep(session, world)

    runs = _list_runs(
        api_client,
        world,
        parent_sweep_id=str(sweep_id),
        sweep_arm="no_ontology",
    )

    assert len(runs) == 1
    assert runs[0]["sweep_arm"] == "no_ontology"


def test_a_non_sweep_run_reports_no_arm(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    solution = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="plain-sut",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=world.alice_id,
    )
    run = RunRepo(session).create(
        team_id=world.acme_team_id,
        suite_id=world.acme_suite_id,
        solution_id=solution.id,
        suite="plain_v1",
        dataset_version="v0",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={},
        created_by=world.alice_id,
    )
    session.commit()

    runs = [row for row in _list_runs(api_client, world) if row["run_id"] == str(run.id)]

    assert len(runs) == 1
    assert runs[0]["sweep_arm"] is None
    assert runs[0]["parent_sweep_id"] is None
