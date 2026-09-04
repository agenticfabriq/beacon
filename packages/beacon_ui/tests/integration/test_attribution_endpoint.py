from typing import Protocol, cast
from uuid import UUID

import pytest
from beacon_storage.ids import uuid7
from beacon_storage.models.attribution import Attribution
from beacon_storage.models.runs import HarnessMode
from beacon_storage.models.solutions import Solution
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    acme_suite_id: UUID
    acme_solution_id: UUID
    alice_id: UUID
    alice_key: str


def _seed_attributions(session: Session, world: _World) -> UUID:
    suite = SuiteRepo(session).get(world.acme_suite_id)
    assert suite is not None

    run_repo = RunRepo(session)
    sweep_id = uuid7()
    baseline = run_repo.create(
        team_id=world.acme_team_id,
        suite_id=suite.id,
        solution_id=world.acme_solution_id,
        suite=suite.name,
        dataset_version="v2",
        mode=HarnessMode.NIGHTLY_LOO,
        pass_idx=0,
        parent_sweep_id=sweep_id,
        config={"label": "baseline"},
        created_by=world.alice_id,
    )
    ablated = run_repo.create(
        team_id=world.acme_team_id,
        suite_id=suite.id,
        solution_id=world.acme_solution_id,
        suite=suite.name,
        dataset_version="v2",
        mode=HarnessMode.NIGHTLY_LOO,
        pass_idx=1,
        parent_sweep_id=sweep_id,
        config={"label": "ablated"},
        created_by=world.alice_id,
    )
    run_repo.mark_completed(baseline.id)
    run_repo.mark_completed(ablated.id)

    for layer_name, delta, bh_p, token_delta in (
        ("ontology", 0.18, 0.006, -0.12),
        ("retry_loop", -0.04, 0.120, 0.08),
    ):
        session.add(
            Attribution(
                attribution_id=uuid7(),
                sweep_id=sweep_id,
                team_id=world.acme_team_id,
                solution_id=world.acme_solution_id,
                solution_version="0.1",
                suite=suite.name,
                dataset_version="v2",
                layer_name=layer_name,
                methodology="LOO",
                baseline_run_id=baseline.id,
                ablated_run_id=ablated.id,
                pass_at_k_baseline={"3": 0.71},
                pass_at_k_ablated={"3": 0.71 + delta},
                delta_pass_at_k={
                    "3": {
                        "delta": delta,
                        "ci_low": delta - 0.02,
                        "ci_high": delta + 0.02,
                        "p": bh_p / 2,
                    }
                },
                pass_hat_k_baseline={"3": 0.69},
                pass_hat_k_ablated={"3": 0.69 + delta},
                delta_pass_hat_k={
                    "3": {
                        "delta": delta,
                        "ci_low": delta - 0.03,
                        "ci_high": delta + 0.03,
                        "p": bh_p / 2,
                    }
                },
                token_delta_pct=token_delta,
                runtime_delta_pct=None,
                mcnemar_p=bh_p / 2,
                bh_adjusted_p=bh_p,
                ci_low=delta - 0.02,
                ci_high=delta + 0.02,
            )
        )
    session.commit()
    return suite.id


def _ensure_layerless_solution(session: Session, world: _World) -> Solution:
    repo = SolutionRepo(session)
    existing = repo.get_by_team_and_solution(world.acme_team_id, "layerless-api-sut", "0.1")
    if existing is not None:
        return existing
    return repo.create(
        team_id=world.acme_team_id,
        solution_id="layerless-api-sut",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="Layerless API SUT",
        supported_modes=["EVAL"],
        layers=[],
        created_by=world.alice_id,
    )


def test_get_attribution_latest_snapshot(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    suite_id = _seed_attributions(session, world)

    response = api_client.get(
        f"/v1/suites/{suite_id}/attribution",
        headers={"X-API-Key": world.alice_key},
        params={"sut": str(world.acme_solution_id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["solution_id"] == str(world.acme_solution_id)
    assert body["suite_id"] == str(suite_id)
    assert body["supported"] is True
    assert body["computed_at"] is not None
    assert [row["layer"] for row in body["layers"]] == ["ontology", "retry_loop"]
    assert body["layers"][0]["delta_pass_at_3"] == pytest.approx(0.18)
    assert body["layers"][0]["ci_low"] == pytest.approx(0.16)
    assert body["layers"][0]["ci_high"] == pytest.approx(0.20)
    assert body["layers"][0]["mcnemar_p"] == pytest.approx(0.003)
    assert body["layers"][0]["bh_p"] == pytest.approx(0.006)
    assert body["layers"][0]["median_token_delta_pct"] == pytest.approx(-0.12)


def test_get_attribution_layerless_solution_is_unsupported(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    suite = SuiteRepo(session).get(world.acme_suite_id)
    assert suite is not None
    solution = _ensure_layerless_solution(session, world)
    session.commit()

    response = api_client.get(
        f"/v1/suites/{suite.id}/attribution",
        headers={"X-API-Key": world.alice_key},
        params={"sut": str(solution.id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["supported"] is False
    assert body["computed_at"] is None
    assert body["layers"] == []


def test_a_legacy_row_reports_its_counts_as_ABSENT_not_zero(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    """A row written before the counts existed does not know them (B64).

    Zero would claim nothing was excluded, which is a measurement nobody took.
    The seed above sets none of the four columns, so this pins the shape of
    every attribution already in a deployment.
    """
    suite_id = _seed_attributions(session, world)

    response = api_client.get(
        f"/v1/suites/{suite_id}/attribution",
        headers={"X-API-Key": world.alice_key},
        params={"sut": str(world.acme_solution_id)},
    )

    assert response.status_code == 200, response.text
    for layer in response.json()["layers"]:
        assert layer["n_compared"] is None, layer["layer"]
        assert layer["n_items_submitted"] is None, layer["layer"]
        assert layer["n_baseline_excluded"] is None, layer["layer"]
        assert layer["n_ablated_excluded"] is None, layer["layer"]


def test_the_endpoint_publishes_what_the_delta_was_measured_over(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    """Recording the exclusion is only half of it; the surface must say so.

    The per-k entry is preferred over the promoted column for the same reason
    the deltas are: it is the value computed for that k. Here they deliberately
    disagree -- 7 in the JSONB, 9 in the column -- so a reader can tell which
    one the endpoint honours. (The first draft of this test used 99, which the
    within-submission check constraint refused against 10 submitted, correctly.)
    """
    from beacon_storage.models.attribution import Attribution
    from sqlalchemy import select

    suite_id = _seed_attributions(session, world)
    row = session.scalars(select(Attribution).where(Attribution.layer_name == "ontology")).one()
    row.n_items_submitted = 10
    row.n_baseline_excluded = 1
    row.n_ablated_excluded = 3
    row.n_compared = 9
    headline = cast("dict[str, object]", row.delta_pass_at_k["3"])
    row.delta_pass_at_k = {**row.delta_pass_at_k, "3": {**headline, "n_compared": 7}}
    session.commit()

    response = api_client.get(
        f"/v1/suites/{suite_id}/attribution",
        headers={"X-API-Key": world.alice_key},
        params={"sut": str(world.acme_solution_id)},
    )

    assert response.status_code == 200, response.text
    ontology = next(row for row in response.json()["layers"] if row["layer"] == "ontology")
    assert ontology["n_compared"] == 7, "the per-k entry wins over the promoted column"
    assert ontology["n_items_submitted"] == 10
    assert ontology["n_baseline_excluded"] == 1
    # The arms lost different numbers of tasks, which is the reading that turns
    # "the delta is small" into "the delta compared two different samples".
    assert ontology["n_ablated_excluded"] == 3


def test_a_compared_count_larger_than_the_submission_is_refused(
    session: Session,
    world: _World,
) -> None:
    """Each arm's tasks come from the submitted items, so no count can exceed it.

    This is the bound no engine mutation reaches -- an inverted subtraction
    yields a NEGATIVE, which this constraint accepts (-8 <= 10) and
    `ck_attribution_sample_non_negative` is what refuses; see the test below.
    It is pinned anyway because it is the bound a future writer would violate
    first, and because it already caught a wrong fixture in this file: the
    sibling test above set 99 compared out of 10 submitted and the database
    refused it.
    """
    import pytest as _pytest
    from beacon_storage.models.attribution import Attribution
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    _seed_attributions(session, world)
    row = session.scalars(select(Attribution).where(Attribution.layer_name == "retry_loop")).one()
    row.n_items_submitted = 10
    row.n_compared = 11

    with _pytest.raises(IntegrityError, match="ck_attribution_sample_within_submission"):
        session.commit()
    session.rollback()


def test_a_negative_count_is_refused(
    session: Session,
    world: _World,
) -> None:
    """The bound an inverted subtraction actually crosses.

    The engine computes `n_items_submitted - len(baseline_at_k)`. Inverted,
    that is <= 0, and the within-submission constraint accepts it: -8 <= 10.
    Only `ck_attribution_sample_non_negative` rejects it, so it is the one
    standing between a sign error and a stored count that reads as a
    measurement of "fewer than none excluded".
    """
    import pytest as _pytest
    from beacon_storage.models.attribution import Attribution
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    _seed_attributions(session, world)
    row = session.scalars(select(Attribution).where(Attribution.layer_name == "ontology")).one()
    row.n_items_submitted = 10
    row.n_baseline_excluded = -8

    with _pytest.raises(IntegrityError, match="ck_attribution_sample_non_negative"):
        session.commit()
    session.rollback()


def test_a_K1_sweep_does_not_get_a_real_sample_size_for_a_fabricated_delta(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    """`delta_pass_at_3` on a K=1 row is a default, not a measurement.

    The engine's headline is `min(3, K)`, so a one-pass sweep -- the default of
    `run_fs_payments_ablation.py` -- stores only a `"1"` entry and
    `_per_k_metric` falls back to its 0.0. Publishing the real k=1 exclusion
    counts beside that zero would turn a detectable fiction into a plausible
    finding: a confident null corroborated by a genuine n. The k-scoped counts
    are withheld instead.

    `n_items_submitted` is not withheld, because it is not scoped to a k --
    it is how many items the sweep was asked to run, true on any row.
    """
    from beacon_storage.models.attribution import Attribution
    from sqlalchemy import select

    suite_id = _seed_attributions(session, world)
    row = session.scalars(select(Attribution).where(Attribution.layer_name == "ontology")).one()
    row.n_items_submitted = 10
    row.n_baseline_excluded = 1
    row.n_ablated_excluded = 3
    row.n_compared = 6
    # A K=1 sweep: headline k is "1", and there is no "3" reading at all.
    row.delta_pass_at_k = {
        "1": {"delta": 0.2, "ci_low": 0.1, "ci_high": 0.3, "p": 0.04, "n_compared": 6}
    }
    session.commit()

    response = api_client.get(
        f"/v1/suites/{suite_id}/attribution",
        headers={"X-API-Key": world.alice_key},
        params={"sut": str(world.acme_solution_id)},
    )

    assert response.status_code == 200, response.text
    ontology = next(row for row in response.json()["layers"] if row["layer"] == "ontology")
    assert ontology["delta_pass_at_3"] == 0.0, "the pre-existing fabricated default"
    assert ontology["n_compared"] is None
    assert ontology["n_baseline_excluded"] is None
    assert ontology["n_ablated_excluded"] is None
    assert ontology["n_items_submitted"] == 10, "k-independent, so still reported"
