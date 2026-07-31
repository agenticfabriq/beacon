"""register_mnemiq_solution: Solution-row upsert + registry wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_runner.registry import SutRegistry
from beacon_runner.sut.mnemiq import MnemiqInProcessSUT, register_mnemiq_solution
from beacon_storage.db import make_session_factory
from beacon_storage.repository.project_solutions import ProjectSolutionRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_register_is_idempotent_and_links_project(engine: Engine) -> None:
    factory = make_session_factory(engine)
    with factory() as session:
        user = UserRepo(session).create(email="mnemiq-reg@example.com", name="mnemiq-reg")
        team = TeamRepo(session).create(name="mnemiq-reg-team")
        project = ProjectRepo(session).create(
            team_id=team.id, name="mnemiq-reg-project", created_by=user.id
        )
        sut = MnemiqInProcessSUT(
            owner_team_id=team.id,
            minidev_dir="/nonexistent/minidev",
            bird_dsn="postgresql://nobody@nowhere/none",
            enrich_cache_dir="/nonexistent/cache",
            engine_builder=lambda _db, _enabled: (lambda _q: None, object()),
        )
        registry = SutRegistry()

        first = register_mnemiq_solution(
            session,
            team_id=team.id,
            created_by=user.id,
            sut=sut,
            project_id=project.id,
            registry=registry,
        )
        second = register_mnemiq_solution(
            session,
            team_id=team.id,
            created_by=user.id,
            sut=sut,
            project_id=project.id,
            registry=registry,
        )
        session.commit()

        assert first.id == second.id
        assert first.solution_id == "mnemiq"
        assert [layer["name"] for layer in first.layers] == [
            "enrichment",
            "grounding",
            "verifier",
            "self_consistency",
            "mode_routing",
        ]
        assert first.supported_modes == ["EVAL", "NIGHTLY_LOO"]
        assert registry.get("mnemiq", MnemiqInProcessSUT.VERSION) is sut

        linked = ProjectSolutionRepo(session).list_with_solution(project.id)
        assert [solution.id for solution, _added in linked] == [first.id]


def test_register_without_project_skips_link(engine: Engine) -> None:
    factory = make_session_factory(engine)
    with factory() as session:
        user = UserRepo(session).create(email=f"mnemiq-{uuid4().hex[:8]}@example.com", name="r")
        team = TeamRepo(session).create(name=f"mnemiq-team-{uuid4().hex[:8]}")
        sut = MnemiqInProcessSUT(
            owner_team_id=team.id,
            minidev_dir="/nonexistent/minidev",
            bird_dsn="postgresql://nobody@nowhere/none",
            enrich_cache_dir="/nonexistent/cache",
            engine_builder=lambda _db, _enabled: (lambda _q: None, object()),
        )
        registry = SutRegistry()
        solution = register_mnemiq_solution(
            session, team_id=team.id, created_by=user.id, sut=sut, registry=registry
        )
        session.commit()
        assert solution.solution_id == "mnemiq"
