"""End-to-end anti-Goodhart worker tick against real Postgres."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from beacon_storage.db import make_session_factory
from beacon_storage.models.antigoodhart import AntigoodhartFinding, AntigoodhartKind
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.repository.antigoodhart import AntigoodhartRepo
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_workers.antigoodhart.worker import AntiGoodhartWorker
from sqlalchemy import select

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _create_context(session: Session) -> tuple[UUID, UUID, UUID]:
    user = UserRepo(session).create(email="ag@example.com", name="Anti Goodhart")
    team = TeamRepo(session).create(name="antigoodhart-team")
    project = ProjectRepo(session).create(
        team_id=team.id,
        name="antigoodhart-project",
        created_by=user.id,
    )
    return team.id, project.id, user.id


def test_antigoodhart_tick_records_findings(engine: Any) -> None:
    factory = make_session_factory(engine)

    with factory() as session:
        team_id, project_id, user_id = _create_context(session)
        suite = SuiteRepo(session).create(
            project_id=project_id,
            team_id=team_id,
            name="antigoodhart-suite",
            description="",
            method="manual",
            suite_metadata={},
            created_by=user_id,
        )
        item_repo = EvalItemRepo(session)
        sql_item = item_repo.create(
            team_id=team_id,
            suite="antigoodhart-suite",
            solution_id=None,
            item_input={"question": ("run SELECT sum(revenue) FROM sales WHERE quarter='Q3'")},
            gold_answer={
                "sql": "SELECT sum(revenue) FROM sales WHERE quarter='Q3'",
            },
            tier=EvalItemTier.MODEL_PROPOSED,
            item_metadata={},
            question_hash="ag-sql",
            embedding=None,
            evidence=None,
            created_by=user_id,
        )
        evidence_item = item_repo.create(
            team_id=team_id,
            suite="antigoodhart-suite",
            solution_id=None,
            item_input={"question": "what was Q3 revenue?"},
            gold_answer={"answer": 4123550},
            tier=EvalItemTier.MODEL_PROPOSED,
            item_metadata={},
            question_hash="ag-evidence",
            embedding=None,
            evidence="Q3 totals: 4123550",
            created_by=user_id,
        )
        clean_item = item_repo.create(
            team_id=team_id,
            suite="antigoodhart-suite",
            solution_id=None,
            item_input={"question": "what was Q3 revenue?"},
            gold_answer={"answer": 999999},
            tier=EvalItemTier.MODEL_PROPOSED,
            item_metadata={"team": "acme"},
            question_hash="ag-clean",
            embedding=None,
            evidence="schema for sales table",
            created_by=user_id,
        )
        SuiteRepo(session).add_items(
            suite_id=suite.id,
            item_ids=[sql_item.item_id, evidence_item.item_id, clean_item.item_id],
        )
        session.commit()
        suite_id = suite.id
        clean_item_id = clean_item.item_id

    worker = AntiGoodhartWorker(
        factory=factory,
        sql_ngram_size=5,
        team_share_threshold=0.6,
        batch_size=100,
    )
    worker.tick()

    with factory() as session:
        latest_scan = AntigoodhartRepo(session).latest_scan_id(
            team_id=team_id,
            suite_id=suite_id,
        )
        assert latest_scan is not None
        findings = AntigoodhartRepo(session).list_for_scan(latest_scan)
        kinds = {finding.kind for finding in findings}
        item_ids = {finding.item_id for finding in findings}

        assert AntigoodhartKind.SQL_IN_QUESTION in kinds
        assert AntigoodhartKind.EVIDENCE_LEAK in kinds
        assert AntigoodhartKind.DISTRIBUTION_SKEW in kinds
        assert clean_item_id not in item_ids


def test_antigoodhart_each_tick_has_new_scan_id(engine: Any) -> None:
    factory = make_session_factory(engine)

    with factory() as session:
        team_id, project_id, user_id = _create_context(session)
        suite = SuiteRepo(session).create(
            project_id=project_id,
            team_id=team_id,
            name="antigoodhart-suite",
            description="",
            method="manual",
            suite_metadata={},
            created_by=user_id,
        )
        item = EvalItemRepo(session).create(
            team_id=team_id,
            suite="antigoodhart-suite",
            solution_id=None,
            item_input={"question": "x?"},
            gold_answer={},
            tier=EvalItemTier.MODEL_PROPOSED,
            item_metadata={"gold_answer": "leaked"},
            question_hash="ag-metadata",
            embedding=None,
            evidence=None,
            created_by=user_id,
        )
        SuiteRepo(session).add_items(suite_id=suite.id, item_ids=[item.item_id])
        session.commit()

    worker = AntiGoodhartWorker(
        factory=factory,
        sql_ngram_size=5,
        team_share_threshold=0.6,
        batch_size=100,
    )
    worker.tick()
    worker.tick()

    with factory() as session:
        scan_ids = session.scalars(
            select(AntigoodhartFinding.scan_id)
            .where(AntigoodhartFinding.team_id == team_id)
            .distinct()
        ).all()

    assert len(scan_ids) == 2
