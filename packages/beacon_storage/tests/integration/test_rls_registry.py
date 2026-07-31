"""RLS for registry tables."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_storage.db import make_session_factory
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.provenance import ActorType
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.production_traces import ProductionTraceRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.provenance import ProvenanceRepo
from beacon_storage.repository.suites import SuiteRepo
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


def test_rls_filters_registry_tables(engine: Engine) -> None:
    factory = make_session_factory(engine)

    with factory() as su:
        alice = UserRepo(su).create(email="alice-rls-reg@example.com", name="Alice")
        bob = UserRepo(su).create(email="bob-rls-reg@example.com", name="Bob")
        carol = UserRepo(su).create(email="carol-rls-reg@example.com", name="Carol")
        team_a = TeamRepo(su).create(name="rls-reg-a")
        team_b = TeamRepo(su).create(name="rls-reg-b")
        project_a = ProjectRepo(su).create(
            team_id=team_a.id,
            name="registry-a",
            created_by=alice.id,
        )
        project_b = ProjectRepo(su).create(
            team_id=team_b.id,
            name="registry-b",
            created_by=bob.id,
        )

        item_repo = EvalItemRepo(su)
        alice_item = item_repo.create(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite="bird",
            team_id=team_a.id,
            item_input={"owner": "alice"},
            gold_answer={},
            created_by=alice.id,
        )
        bob_item = item_repo.create(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite="bird",
            team_id=team_b.id,
            item_input={"owner": "bob"},
            gold_answer={},
            created_by=bob.id,
        )
        shared_item = item_repo.create(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite="bird",
            team_id=None,
            item_input={"owner": "shared"},
            gold_answer={},
            created_by=alice.id,
        )

        prov_repo = ProvenanceRepo(su)
        prov_repo.append(
            item_id=alice_item.item_id,
            team_id=team_a.id,
            prior_tier=None,
            new_tier=EvalItemTier.MODEL_PROPOSED,
            actor_type=ActorType.SYSTEM,
            actor_id="ingest",
            created_by=None,
            reason="alice item",
            evidence={},
        )
        prov_repo.append(
            item_id=bob_item.item_id,
            team_id=team_b.id,
            prior_tier=None,
            new_tier=EvalItemTier.MODEL_PROPOSED,
            actor_type=ActorType.SYSTEM,
            actor_id="ingest",
            created_by=None,
            reason="bob item",
            evidence={},
        )

        suite_repo = SuiteRepo(su)
        alice_suite = suite_repo.create(
            project_id=project_a.id,
            team_id=team_a.id,
            name="alice-suite",
            description="",
            method="manual",
            suite_metadata={},
            created_by=alice.id,
        )
        bob_suite = suite_repo.create(
            project_id=project_b.id,
            team_id=team_b.id,
            name="bob-suite",
            description="",
            method="manual",
            suite_metadata={},
            created_by=bob.id,
        )
        suite_repo.add_items(suite_id=alice_suite.id, item_ids=[alice_item.item_id])
        suite_repo.add_items(suite_id=bob_suite.id, item_ids=[bob_item.item_id])

        trace_repo = ProductionTraceRepo(su)
        trace_repo.create(
            team_id=team_a.id,
            project_id=project_a.id,
            solution_id="acme-chat-to-data",
            api_key_id=None,
            item_input={"owner": "alice"},
            item_output={"answer": "a"},
            trace_payload={"name": "root"},
            trace_metadata={},
            is_eval_candidate=True,
            derived_trace_id=None,
        )
        trace_repo.create(
            team_id=team_b.id,
            project_id=project_b.id,
            solution_id="acme-chat-to-data",
            api_key_id=None,
            item_input={"owner": "bob"},
            item_output={"answer": "b"},
            trace_payload={"name": "root"},
            trace_metadata={},
            is_eval_candidate=True,
            derived_trace_id=None,
        )

        membership_repo = MembershipRepo(su)
        membership_repo.grant(
            user_id=alice.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_a.id,
            role=Role.TEAM_ADMIN,
        )
        membership_repo.grant(
            user_id=bob.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_b.id,
            role=Role.TEAM_ADMIN,
        )
        membership_repo.grant(
            user_id=carol.id,
            scope_kind=ScopeKind.PROJECT,
            scope_id=project_a.id,
            role=Role.PROJECT_VIEWER,
        )
        su.commit()

        alice_id = alice.id
        bob_id = bob.id
        carol_id = carol.id
        alice_item_id = alice_item.item_id
        bob_item_id = bob_item.item_id
        shared_item_id = shared_item.item_id
        project_a_id = project_a.id
        project_b_id = project_b.id
        alice_suite_id = alice_suite.id
        bob_suite_id = bob_suite.id

    with factory() as s:
        _use_app_role(s)
        set_current_user(s, bob_id)

        visible_items = EvalItemRepo(s).list_active(
            suite="bird",
            tier=EvalItemTier.HUMAN_VERIFIED,
        )
        assert {item.item_id for item in visible_items} == {bob_item_id, shared_item_id}
        assert ProvenanceRepo(s).list_for_item(alice_item_id) == []
        assert len(ProvenanceRepo(s).list_for_item(bob_item_id)) == 1
        assert SuiteRepo(s).list_for_project(project_a_id) == []
        assert [suite.id for suite in SuiteRepo(s).list_for_project(project_b_id)] == [bob_suite_id]
        assert SuiteRepo(s).list_item_ids(alice_suite_id) == []
        assert {
            trace.item_input["owner"] for trace in ProductionTraceRepo(s).list_unprocessed()
        } == {"bob"}

    with factory() as s:
        _use_app_role(s)
        set_current_user(s, carol_id)

        visible_items = EvalItemRepo(s).list_active(
            suite="bird",
            tier=EvalItemTier.HUMAN_VERIFIED,
        )
        assert {item.item_id for item in visible_items} == {shared_item_id}
        assert ProvenanceRepo(s).list_for_item(alice_item_id) == []
        assert [suite.id for suite in SuiteRepo(s).list_for_project(project_a_id)] == [
            alice_suite_id
        ]
        assert SuiteRepo(s).list_item_ids(alice_suite_id) == [alice_item_id]
        assert ProductionTraceRepo(s).list_unprocessed() == []

    with factory() as s:
        _use_app_role(s)
        set_current_user(s, alice_id)

        visible_items = EvalItemRepo(s).list_active(
            suite="bird",
            tier=EvalItemTier.HUMAN_VERIFIED,
        )
        assert {item.item_id for item in visible_items} == {alice_item_id, shared_item_id}
        assert len(ProvenanceRepo(s).list_for_item(alice_item_id)) == 1
        assert [suite.id for suite in SuiteRepo(s).list_for_project(project_a_id)] == [
            alice_suite_id
        ]
        assert SuiteRepo(s).list_item_ids(alice_suite_id) == [alice_item_id]
        assert {
            trace.item_input["owner"] for trace in ProductionTraceRepo(s).list_unprocessed()
        } == {"alice"}
