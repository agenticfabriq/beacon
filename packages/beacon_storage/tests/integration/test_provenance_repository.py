"""ProvenanceRepo append-only writes and chronological listing."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.provenance import ActorType
from beacon_storage.models.tenancy import Team, User
from beacon_storage.repository.provenance import ProvenanceRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User]:
    team = Team(name="prov-repo")
    user = User(email="pr@example.com", name="P")
    session.add_all([team, user])
    session.flush()
    return team, user


def test_append_and_list_for_item(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    repo = ProvenanceRepo(session)
    item_id = uuid4()
    repo.append(
        item_id=item_id,
        team_id=team.id,
        prior_tier=None,
        new_tier=EvalItemTier.MODEL_PROPOSED,
        actor_type=ActorType.SYSTEM,
        actor_id="ingest",
        created_by=None,
        reason="ingested",
        evidence={},
    )
    repo.append(
        item_id=item_id,
        team_id=team.id,
        prior_tier=EvalItemTier.MODEL_PROPOSED,
        new_tier=EvalItemTier.EXECUTION_CONFIRMED,
        actor_type=ActorType.SYSTEM,
        actor_id="convergence_worker",
        created_by=None,
        reason="3 SUTs converged",
        evidence={"converging_runs": [str(uuid4())]},
    )
    repo.append(
        item_id=item_id,
        team_id=team.id,
        prior_tier=EvalItemTier.EXECUTION_CONFIRMED,
        new_tier=EvalItemTier.HUMAN_VERIFIED,
        actor_type=ActorType.HUMAN,
        actor_id=str(user.id),
        created_by=user.id,
        reason="reviewed",
        evidence={},
    )

    history = repo.list_for_item(item_id)

    assert [event.new_tier for event in history] == [
        EvalItemTier.MODEL_PROPOSED,
        EvalItemTier.EXECUTION_CONFIRMED,
        EvalItemTier.HUMAN_VERIFIED,
    ]
