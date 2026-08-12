"""Pushing execution outputs into a registered run (B17).

Beacon registers a run, someone else executes it elsewhere, and the outputs
come back to be graded here. What is pushed is output, never a verdict: the
system under test does not score its own work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID  # noqa: TC003

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.runs import HarnessMode, RunStatus
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

SUITE = "ingest_answer_v1"


class _World(Protocol):
    acme_team_id: UUID
    alice_id: UUID
    alice_key: str


def _seed(session: Session, world: _World) -> tuple[str, str]:
    """Register a run awaiting results, plus one answer-graded item."""
    solution = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="ingest-sut",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=world.alice_id,
    )
    item = EvalItemRepo(session).create(
        tier=EvalItemTier.HUMAN_VERIFIED,
        suite=SUITE,
        team_id=world.acme_team_id,
        dataset_version="v1",
        item_input={"question": "what is yes?"},
        gold_answer={"answer": "yes"},
        item_metadata={},
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
    run = RunRepo(session).create(
        team_id=world.acme_team_id,
        suite_id=suite.id,
        solution_id=solution.id,
        suite=SUITE,
        dataset_version="v1",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={},
        created_by=world.alice_id,
    )
    session.commit()
    return str(run.id), str(item.item_id)


def _push(
    api_client: TestClient,
    world: _World,
    run_id: str,
    body: dict[str, Any],
) -> Any:
    return api_client.post(
        f"/v1/runs/{run_id}/results",
        headers={"X-API-Key": world.alice_key},
        json=body,
    )


def _payload(item_id: str, answer: str, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "item_id": item_id,
        "attempt_idx": 0,
        "output": {"answer": answer},
        "output_kind": "answer",
        "tokens_input": 10,
        "tokens_output": 5,
        "runtime_ms": 100,
    }
    body.update(over)
    return body


def test_a_pushed_output_is_graded_here(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """Beacon computes the verdict; the pusher only supplies the answer."""
    run_id, item_id = _seed(session, world)

    response = _push(api_client, world, run_id, _payload(item_id, "yes"))

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "PASS"
    assert response.json()["created"] is True


def test_a_wrong_answer_grades_fail(
    api_client: TestClient, world: _World, session: Session
) -> None:
    run_id, item_id = _seed(session, world)

    response = _push(api_client, world, run_id, _payload(item_id, "no"))

    assert response.json()["outcome"] == "FAIL"


def test_a_declined_answer_grades_defer(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """The tracker's whole point: declining is not failing (B16)."""
    run_id, item_id = _seed(session, world)

    response = _push(api_client, world, run_id, _payload(item_id, "", deferred=True))

    assert response.json()["outcome"] == "DEFER"


def test_a_declined_answer_stays_declined_in_storage(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """The refusal must survive in ``output``, or a regrade reverses it.

    ``deferred`` arrives as a first-class field of the push, but only
    ``output`` is persisted and a regrade rebuilds its ExecutionResult from
    ``output`` alone. A pusher that set the documented top-level field and
    nothing else would grade DEFER here and FAIL on the next regrade -- and on
    an unanswerable item, where declining is the right answer, that is a PASS
    silently becoming a FAIL.
    """
    run_id, item_id = _seed(session, world)

    _push(api_client, world, run_id, _payload(item_id, "", deferred=True))

    stored = ResultRepo(session).list_for_run(UUID(run_id))
    assert [row.output.get("deferred") for row in stored] == [True]


def test_pushing_marks_the_run_running(
    api_client: TestClient, world: _World, session: Session
) -> None:
    run_id, item_id = _seed(session, world)

    _push(api_client, world, run_id, _payload(item_id, "yes"))

    session.expire_all()
    run = RunRepo(session).get(UUID(run_id))
    assert run is not None
    assert run.status == RunStatus.RUNNING


def test_an_identical_repush_is_accepted_and_does_not_duplicate(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """A client that lost our response must be able to retry safely."""
    run_id, item_id = _seed(session, world)
    body = _payload(item_id, "yes")

    first = _push(api_client, world, run_id, body)
    second = _push(api_client, world, run_id, body)

    assert first.json()["created"] is True
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["outcome"] == "PASS"


def test_a_different_payload_for_the_same_attempt_conflicts(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """Overwriting would destroy the evidence a verdict was computed from."""
    run_id, item_id = _seed(session, world)
    _push(api_client, world, run_id, _payload(item_id, "yes"))

    response = _push(api_client, world, run_id, _payload(item_id, "no"))

    assert response.status_code == 409, response.text
    assert "already has a different result" in response.text


def test_a_second_attempt_is_a_new_row(
    api_client: TestClient, world: _World, session: Session
) -> None:
    run_id, item_id = _seed(session, world)
    _push(api_client, world, run_id, _payload(item_id, "yes"))

    response = _push(api_client, world, run_id, _payload(item_id, "no", attempt_idx=1))

    assert response.status_code == 200, response.text
    assert response.json()["created"] is True


def test_an_unknown_item_is_rejected(
    api_client: TestClient, world: _World, session: Session
) -> None:
    run_id, _ = _seed(session, world)

    response = _push(
        api_client, world, run_id, _payload("06a6ce57-0000-7000-8000-000000000000", "yes")
    )

    assert response.status_code == 404


def test_completing_a_run_closes_it(
    api_client: TestClient, world: _World, session: Session
) -> None:
    run_id, item_id = _seed(session, world)
    _push(api_client, world, run_id, _payload(item_id, "yes"))

    response = api_client.post(
        f"/v1/runs/{run_id}/complete",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert response.json()["n_results"] == 1


def test_a_closed_run_refuses_further_results(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """Late arrivals must not silently change a run someone has already read."""
    run_id, item_id = _seed(session, world)
    api_client.post(
        f"/v1/runs/{run_id}/complete",
        headers={"X-API-Key": world.alice_key},
    )

    response = _push(api_client, world, run_id, _payload(item_id, "yes"))

    assert response.status_code == 409, response.text
    assert "awaiting them" in response.text


def test_a_trace_contradicting_the_declared_arm_is_refused(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """B22: an ablated arm whose trace shows the layer ran is not that experiment.

    The attribution engine would compare it against the baseline as though the
    layer had been removed, and attribute the difference to a layer that was
    never switched off.
    """
    run_id, item_id = _seed(session, world)
    RunRepo(session).get(UUID(run_id)).config = {  # type: ignore[union-attr]
        "layers_enabled": {"self_consistency": False},
    }
    session.commit()

    response = _push(
        api_client,
        world,
        run_id,
        _payload(
            item_id,
            "yes",
            trace={
                "uuid": "root",
                "name": "run",
                "level": "workflow",
                "status": "COMPLETED",
                "children": [
                    {
                        "uuid": "sc",
                        "name": "self_consistency",
                        "level": "layer:self_consistency",
                        "status": "COMPLETED",
                    }
                ],
            },
        ),
    )

    assert response.status_code == 409, response.text
    assert "declared disabled" in response.json()["detail"]


def test_a_trace_agreeing_with_the_declared_arm_is_accepted(
    api_client: TestClient, world: _World, session: Session
) -> None:
    run_id, item_id = _seed(session, world)
    RunRepo(session).get(UUID(run_id)).config = {  # type: ignore[union-attr]
        "layers_enabled": {"self_consistency": False},
    }
    session.commit()

    response = _push(
        api_client,
        world,
        run_id,
        _payload(
            item_id,
            "yes",
            trace={
                "uuid": "root",
                "name": "run",
                "level": "workflow",
                "status": "COMPLETED",
                "children": [
                    {
                        "uuid": "sc",
                        "name": "self_consistency",
                        "level": "layer:self_consistency",
                        "status": "SKIPPED",
                    }
                ],
            },
        ),
    )

    assert response.status_code == 200, response.text
