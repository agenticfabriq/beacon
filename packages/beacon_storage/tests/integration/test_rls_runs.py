"""RLS for run tables: Bob cannot see Alice's project's runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_storage.db import make_session_factory
from beacon_storage.models.runs import HarnessMode
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _use_app_role(session: Session) -> None:
    session.execute(text("SET LOCAL ROLE beacon_app"))


def test_rls_hides_other_projects_runs(engine: Engine) -> None:
    factory = make_session_factory(engine)
    with factory() as su:
        alice = UserRepo(su).create(email="a-rls@o.com", name="A")
        bob = UserRepo(su).create(email="b-rls@o.com", name="B")
        team_a = TeamRepo(su).create(name="rls-a")
        team_b = TeamRepo(su).create(name="rls-b")
        proj_a = ProjectRepo(su).create(team_id=team_a.id, name="pa", created_by=alice.id)
        sol_a = SolutionRepo(su).create(
            team_id=team_a.id,
            solution_id="dummy",
            version="0.2",
            owner_team=team_a.id,
            summary="",
            supported_modes=["EVAL"],
            layers=[],
            created_by=alice.id,
        )
        RunRepo(su).create(
            team_id=team_a.id,
            project_id=proj_a.id,
            solution_id=sol_a.id,
            suite="s",
            dataset_version="v0",
            mode=HarnessMode.EVAL,
            pass_idx=0,
            config={},
            created_by=alice.id,
        )
        MembershipRepo(su).grant(
            user_id=alice.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_a.id,
            role=Role.TEAM_ADMIN,
        )
        MembershipRepo(su).grant(
            user_id=bob.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_b.id,
            role=Role.TEAM_ADMIN,
        )
        su.commit()
        alice_id = alice.id
        bob_id = bob.id
        proj_a_id = proj_a.id

    with factory() as s:
        _use_app_role(s)
        set_current_user(s, bob_id)
        assert RunRepo(s).list_for_project(proj_a_id) == []

    with factory() as s:
        _use_app_role(s)
        set_current_user(s, alice_id)
        runs = RunRepo(s).list_for_project(proj_a_id)
        assert len(runs) == 1
