"""Retiring a bad experiment without erasing it.

Deleting a run collapses "this run was invalid" into "this run never happened",
which is the conflation that already made an errored arm read as a score of
zero. So the row and its results stay, carrying who retired it and why, and it
leaves every aggregate. These pin both halves: that it stops counting, and that
it is still there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

import pytest
from beacon_storage.models.runs import HarnessMode, ResultStatus, RunStatus, VerdictOutcome
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo

if TYPE_CHECKING:
    # TestClient is built on httpx2, so its Response is not httpx.Response.
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

SUITE = "invalidation_v1"


class _World(Protocol):
    acme_team_id: UUID
    alice_id: UUID
    alice_key: str
    bob_key: str


@dataclass(frozen=True)
class Seeded:
    suite_id: str
    run_id: str
    other_run_id: str


@pytest.fixture
def seeded(session: Session, world: _World) -> Seeded:
    solution = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="invalidation-sut",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=world.alice_id,
    )
    suite = SuiteRepo(session).create(
        team_id=world.acme_team_id,
        name=SUITE,
        description="",
        method="manual",
        suite_metadata={},
        created_by=world.alice_id,
    )
    ids: list[str] = []
    for index in range(2):
        run = RunRepo(session).create(
            team_id=world.acme_team_id,
            suite_id=suite.id,
            solution_id=solution.id,
            suite=SUITE,
            dataset_version="v1",
            mode=HarnessMode.EVAL,
            pass_idx=index,
            config={},
            created_by=world.alice_id,
        )
        RunRepo(session).mark_completed(run.id)
        ResultRepo(session).create(
            team_id=world.acme_team_id,
            run_id=run.id,
            item_id=str(uuid4()),
            attempt_idx=0,
            output={"answer": "a"},
            output_kind="json",
            tokens_input=1,
            tokens_output=1,
            runtime_ms=1,
            status=ResultStatus.COMPLETED,
            outcome=VerdictOutcome.PASS,
            error=None,
        )
        ids.append(str(run.id))
    session.commit()
    return Seeded(suite_id=str(suite.id), run_id=ids[0], other_run_id=ids[1])


def _headers(world: _World) -> dict[str, str]:
    return {"X-API-Key": world.alice_key}


def _invalidate(
    api_client: TestClient, world: _World, run_id: str, reason: str = "Wrong benchmark database"
) -> Any:
    return api_client.post(
        f"/v1/runs/{run_id}/invalidate",
        headers=_headers(world),
        json={"reason": reason},
    )


def test_a_run_can_be_invalidated_with_a_reason(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    response = _invalidate(api_client, world, seeded.run_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["invalidated_at"] is not None
    assert body["invalidation_reason"] == "Wrong benchmark database"


def test_an_invalidation_needs_a_reason(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    """Without one this is a deletion with extra steps."""
    response = _invalidate(api_client, world, seeded.run_id, reason="")

    assert response.status_code == 422


def test_an_invalidated_run_leaves_the_default_listing(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    _invalidate(api_client, world, seeded.run_id)

    listed = api_client.get(
        f"/v1/suites/{seeded.suite_id}/runs", headers=_headers(world)
    ).json()

    ids = [row["run_id"] for row in listed]
    assert seeded.run_id not in ids
    assert seeded.other_run_id in ids


def test_an_invalidated_run_can_still_be_listed_on_request(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    """It is retired, not erased: someone must be able to see what was dropped."""
    _invalidate(api_client, world, seeded.run_id)

    listed = api_client.get(
        f"/v1/suites/{seeded.suite_id}/runs",
        params={"include_invalidated": True},
        headers=_headers(world),
    ).json()

    row = next(item for item in listed if item["run_id"] == seeded.run_id)
    assert row["invalidation_reason"] == "Wrong benchmark database"


def test_its_results_survive(api_client: TestClient, world: _World, seeded: Seeded) -> None:
    """The record has to stay honest: the answers are still there to inspect."""
    _invalidate(api_client, world, seeded.run_id)

    response = api_client.get(
        f"/v1/runs/{seeded.run_id}/results",
        headers=_headers(world),
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_it_is_still_readable_by_id(api_client: TestClient, world: _World, seeded: Seeded) -> None:
    _invalidate(api_client, world, seeded.run_id)

    response = api_client.get(
        f"/v1/runs/{seeded.run_id}", headers=_headers(world)
    )

    assert response.status_code == 200
    assert response.json()["invalidated_at"] is not None


def test_invalidating_clears_the_reference_pin(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    """A retired run cannot go on being what everything is compared against."""
    api_client.patch(
        f"/v1/suites/{seeded.suite_id}",
        headers=_headers(world),
        json={"baseline_run_id": seeded.run_id},
    )

    _invalidate(api_client, world, seeded.run_id)

    settings = api_client.patch(
        f"/v1/suites/{seeded.suite_id}", headers=_headers(world), json={}
    ).json()
    assert settings["baseline_run_id"] is None


def test_invalidating_leaves_an_unrelated_pin_alone(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    api_client.patch(
        f"/v1/suites/{seeded.suite_id}",
        headers=_headers(world),
        json={"baseline_run_id": seeded.other_run_id},
    )

    _invalidate(api_client, world, seeded.run_id)

    settings = api_client.patch(
        f"/v1/suites/{seeded.suite_id}", headers=_headers(world), json={}
    ).json()
    assert settings["baseline_run_id"] == seeded.other_run_id


def test_invalidating_twice_is_a_conflict(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    _invalidate(api_client, world, seeded.run_id)

    second = _invalidate(api_client, world, seeded.run_id, reason="again")

    assert second.status_code == 409


def test_an_invalidation_can_be_undone(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    """Retiring by mistake must not be permanent, or people reach for SQL."""
    _invalidate(api_client, world, seeded.run_id)

    restored = api_client.post(
        f"/v1/runs/{seeded.run_id}/restore",
        headers=_headers(world),
    )

    assert restored.status_code == 200
    assert restored.json()["invalidated_at"] is None
    listed = api_client.get(
        f"/v1/suites/{seeded.suite_id}/runs", headers=_headers(world)
    ).json()
    assert seeded.run_id in [row["run_id"] for row in listed]


def test_restoring_a_valid_run_is_a_conflict(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    response = api_client.post(
        f"/v1/runs/{seeded.run_id}/restore",
        headers=_headers(world),
    )

    assert response.status_code == 409


def test_someone_who_may_only_run_evals_cannot_invalidate(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    response = api_client.post(
        f"/v1/runs/{seeded.run_id}/invalidate",
        headers={"X-API-Key": world.bob_key},
        json={"reason": "not mine to retire"},
    )

    # Retiring a run changes what everyone else sees, and can clear the
    # reference pin, so it takes EVAL_MANAGE rather than EVAL_RUN.
    assert response.status_code == 403


def test_invalidating_an_unknown_run_is_a_404(api_client: TestClient, world: _World) -> None:
    response = _invalidate(api_client, world, str(uuid4()))

    assert response.status_code == 404


def test_the_run_keeps_its_execution_status(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    """Invalidation is orthogonal to how the run ended; both facts matter."""
    body = _invalidate(api_client, world, seeded.run_id).json()

    assert body["status"] == RunStatus.COMPLETED.value
    assert body["invalidated_at"] is not None


def test_retiring_a_run_mid_flight_stops_it_running(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """A run retired while RUNNING is cancelled, not left running forever.

    The orthogonality above holds for a run that ENDED: completed stays
    completed. A run that never ended has no such fact to preserve, and
    leaving it RUNNING makes a status query report live runs that died days
    ago -- two aborted sweep arms sat that way until someone querying by
    status had to be told to read a second column.
    """
    RunRepo(session).mark_running(UUID(seeded.other_run_id))
    session.commit()

    body = _invalidate(api_client, world, seeded.other_run_id).json()

    assert body["status"] == RunStatus.CANCELLED.value
    assert body["invalidated_at"] is not None
    assert body["completed_at"] is not None


def test_restoring_an_in_flight_run_lets_it_be_pushed_to_again(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """Undoing the mistake must undo the cancellation with it.

    Results may only be pushed to a PENDING or RUNNING run. If restore left
    the run CANCELLED it would accept nothing and could not be completed
    either -- bricked by the very action that exists to be safe -- while its
    partial results kept counting in the matrix.
    """
    RunRepo(session).mark_running(UUID(seeded.other_run_id))
    session.commit()
    _invalidate(api_client, world, seeded.other_run_id)

    body = api_client.post(
        f"/v1/runs/{seeded.other_run_id}/restore", headers=_headers(world)
    ).json()

    assert body["invalidated_at"] is None
    assert body["status"] == RunStatus.RUNNING.value
    assert body["completed_at"] is None


def test_restoring_does_not_resurrect_a_run_that_never_started(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """started_at is the evidence for which state to return to."""
    run = RunRepo(session).create(
        team_id=world.acme_team_id,
        suite_id=UUID(seeded.suite_id),
        solution_id=RunRepo(session).get(UUID(seeded.run_id)).solution_id,  # type: ignore[union-attr]
        suite=SUITE,
        dataset_version="v1",
        mode=HarnessMode.EVAL,
        pass_idx=7,
        config={},
        created_by=world.alice_id,
    )
    session.commit()
    _invalidate(api_client, world, str(run.id))

    body = api_client.post(f"/v1/runs/{run.id}/restore", headers=_headers(world)).json()

    # The API presents PENDING as "queued" (_run_status); the column holds PENDING.
    assert body["status"] == "queued"
    session.expire_all()
    assert RunRepo(session).get(run.id).status == RunStatus.PENDING  # type: ignore[union-attr]
