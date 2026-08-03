"""Verify the attributions migration creates table, indexes, and RLS."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from beacon_storage.db import make_session_factory
from beacon_storage.ids import uuid7
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from sqlalchemy import inspect, text

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _use_app_role(session: Session) -> None:
    session.execute(text("SET LOCAL ROLE beacon_app"))


def test_attributions_table_exists(engine: Engine) -> None:
    inspector = inspect(engine)

    assert "attributions" in inspector.get_table_names()


def test_attributions_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("attributions")}
    expected = {
        "attribution_id",
        "sweep_id",
        "team_id",
        "solution_id",
        "solution_version",
        "suite",
        "dataset_version",
        "layer_name",
        "methodology",
        "baseline_run_id",
        "ablated_run_id",
        "pass_at_k_baseline",
        "pass_at_k_ablated",
        "delta_pass_at_k",
        "pass_hat_k_baseline",
        "pass_hat_k_ablated",
        "delta_pass_hat_k",
        "token_delta_pct",
        "runtime_delta_pct",
        "mcnemar_p",
        "bh_adjusted_p",
        "ci_low",
        "ci_high",
        "created_at",
    }

    assert expected.issubset(columns), f"missing columns: {expected - set(columns)}"


def test_attributions_indexes(engine: Engine) -> None:
    inspector = inspect(engine)
    index_names = {index["name"] for index in inspector.get_indexes("attributions")}

    assert "idx_attributions_sweep" in index_names
    assert "idx_attributions_team_solution" in index_names


def test_attributions_rls_hides_rows_from_non_members(engine: Engine) -> None:
    factory = make_session_factory(engine)
    with factory() as superuser:
        alice = UserRepo(superuser).create(email="attr-alice@example.com", name="Alice")
        bob = UserRepo(superuser).create(email="attr-bob@example.com", name="Bob")
        team_a = TeamRepo(superuser).create(name="attr-team-a")
        team_b = TeamRepo(superuser).create(name="attr-team-b")
        _ = SuiteRepo(superuser).create(
            team_id=team_a.id,
            name="attr-project-a",
            description="",
            method="manual",
            suite_metadata={},
            created_by=alice.id,
        )
        solution = SolutionRepo(superuser).create(
            team_id=team_a.id,
            solution_id="dummy",
            version="0.2",
            owner_team=team_a.id,
            summary="",
            supported_modes=["NIGHTLY_LOO"],
            layers=[],
            created_by=alice.id,
        )
        MembershipRepo(superuser).grant(
            user_id=alice.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_a.id,
            role=Role.TEAM_ADMIN,
        )
        MembershipRepo(superuser).grant(
            user_id=bob.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_b.id,
            role=Role.TEAM_ADMIN,
        )
        attribution_id = uuid7()
        _insert_attribution(
            superuser,
            attribution_id=attribution_id,
            team_id=team_a.id,
            solution_id=solution.id,
        )
        superuser.commit()
        alice_id = alice.id
        bob_id = bob.id

    with factory() as session:
        _use_app_role(session)
        set_current_user(session, bob_id)
        count = session.execute(
            text("SELECT count(*) FROM attributions WHERE attribution_id = :id"),
            {"id": attribution_id},
        ).scalar_one()
        assert count == 0

    with factory() as session:
        _use_app_role(session)
        set_current_user(session, alice_id)
        count = session.execute(
            text("SELECT count(*) FROM attributions WHERE attribution_id = :id"),
            {"id": attribution_id},
        ).scalar_one()
        assert count == 1


def _insert_attribution(
    session: Session,
    *,
    attribution_id: UUID,
    team_id: UUID,
    solution_id: UUID,
) -> None:
    metrics = json.dumps({"1": 1.0})
    deltas = json.dumps({"1": {"delta": 0.0, "ci_low": 0.0, "ci_high": 0.0, "p": 1.0}})
    session.execute(
        text(
            """
            INSERT INTO attributions (
                attribution_id, sweep_id, team_id, solution_id,
                solution_version, suite, dataset_version, layer_name, methodology,
                baseline_run_id, ablated_run_id,
                pass_at_k_baseline, pass_at_k_ablated, delta_pass_at_k,
                pass_hat_k_baseline, pass_hat_k_ablated, delta_pass_hat_k,
                token_delta_pct, runtime_delta_pct,
                mcnemar_p, bh_adjusted_p, ci_low, ci_high
            )
            VALUES (
                :attribution_id, :sweep_id, :team_id, :solution_id,
                '0.2', 'suite', 'v0', 'ontology', 'LOO',
                :baseline_run_id, :ablated_run_id,
                CAST(:pass_at_k_baseline AS jsonb),
                CAST(:pass_at_k_ablated AS jsonb),
                CAST(:delta_pass_at_k AS jsonb),
                CAST(:pass_hat_k_baseline AS jsonb),
                CAST(:pass_hat_k_ablated AS jsonb),
                CAST(:delta_pass_hat_k AS jsonb),
                0.0, 0.0, 1.0, 1.0, 0.0, 0.0
            )
            """
        ),
        {
            "attribution_id": attribution_id,
            "sweep_id": uuid7(),
            "team_id": team_id,
            "solution_id": solution_id,
            "baseline_run_id": uuid7(),
            "ablated_run_id": uuid7(),
            "pass_at_k_baseline": metrics,
            "pass_at_k_ablated": metrics,
            "delta_pass_at_k": deltas,
            "pass_hat_k_baseline": metrics,
            "pass_hat_k_ablated": metrics,
            "delta_pass_hat_k": deltas,
        },
    )
