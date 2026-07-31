"""Solution model: team-scoped SUT catalog entry. Stores declared layers as JSONB."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_storage.models.solutions import Solution
from beacon_storage.models.tenancy import Team, User
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.mark.integration
def test_create_solution_with_empty_layers(session: Session) -> None:
    t = Team(name="acme-p2")
    u = User(email="creator@example.com", name="Creator")
    session.add_all([t, u])
    session.flush()
    s = Solution(
        team_id=t.id,
        solution_id="dummy",
        version="0.2.0",
        owner_team=t.id,
        summary="canned",
        supported_modes=["EVAL"],
        layers=[],
        created_by=u.id,
    )
    session.add(s)
    session.commit()
    assert s.id is not None
    assert s.layers == []


@pytest.mark.integration
def test_create_solution_with_two_layers(session: Session) -> None:
    t = Team(name="acme-p2b")
    u = User(email="creator2@example.com", name="Creator")
    session.add_all([t, u])
    session.flush()
    layers = [
        {
            "name": "ontology",
            "description": "...",
            "ablation_semantic": "...",
            "instrumentation": "synthetic",
        },
        {
            "name": "retry_loop",
            "description": "...",
            "ablation_semantic": "...",
            "instrumentation": "synthetic",
        },
    ]
    s = Solution(
        team_id=t.id,
        solution_id="dummy",
        version="0.2.0",
        owner_team=t.id,
        summary="canned",
        supported_modes=["EVAL"],
        layers=layers,
        created_by=u.id,
    )
    session.add(s)
    session.commit()
    assert len(s.layers) == 2
    assert s.layers[0]["name"] == "ontology"


@pytest.mark.integration
def test_solution_id_unique_per_team_version(session: Session) -> None:
    t = Team(name="acme-p2c")
    u = User(email="creator3@example.com", name="Creator")
    session.add_all([t, u])
    session.flush()
    Solution(
        team_id=t.id,
        solution_id="dummy",
        version="0.1",
        owner_team=t.id,
        summary="x",
        supported_modes=["EVAL"],
        layers=[],
        created_by=u.id,
    )
    s1 = Solution(
        team_id=t.id,
        solution_id="dummy",
        version="0.1",
        owner_team=t.id,
        summary="x",
        supported_modes=["EVAL"],
        layers=[],
        created_by=u.id,
    )
    session.add(s1)
    session.commit()
    s2 = Solution(
        team_id=t.id,
        solution_id="dummy",
        version="0.1",
        owner_team=t.id,
        summary="x",
        supported_modes=["EVAL"],
        layers=[],
        created_by=u.id,
    )
    session.add(s2)
    with pytest.raises(IntegrityError):
        session.commit()
