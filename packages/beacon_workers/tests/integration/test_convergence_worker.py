"""End-to-end convergence-worker tick against real Postgres."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from beacon_storage.db import make_session_factory
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.provenance import ActorType
from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.provenance import ProvenanceRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.repository.verdicts import VerdictRepo
from beacon_storage.repository.worker_state import WorkerStateRepo
from beacon_workers.convergence.worker import ConvergenceWorker

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _create_result_with_verdict(
    session: Session,
    *,
    team_id: UUID,
    project_id: UUID,
    user_id: UUID,
    item_id: UUID,
    solution_label: str,
    answer_hash: str,
    canonical_answer: dict[str, object],
    confidence: float = 0.95,
) -> None:
    solution = SolutionRepo(session).create(
        team_id=team_id,
        solution_id=solution_label,
        version="0.2.0",
        owner_team=team_id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=user_id,
    )
    run = RunRepo(session).create(
        team_id=team_id,
        project_id=project_id,
        solution_id=solution.id,
        suite="conv-suite",
        dataset_version="v0",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={},
        created_by=user_id,
    )
    result = ResultRepo(session).create(
        team_id=team_id,
        project_id=project_id,
        run_id=run.id,
        item_id=str(item_id),
        attempt_idx=0,
        output={"sql": "SELECT 1"},
        output_kind="sql",
        tokens_input=1,
        tokens_output=1,
        runtime_ms=1,
        status=ResultStatus.COMPLETED,
        outcome=VerdictOutcome.PASS,
        error=None,
    )
    VerdictRepo(session).create(
        team_id=team_id,
        project_id=project_id,
        result_id=result.id,
        grader="execution_grounded_sql",
        grader_version="v1",
        criterion="correctness",
        bool_value=True,
        value=1.0,
        justification="ok",
        raw_output={},
        answer_hash=answer_hash,
        canonical_answer=canonical_answer,
        confidence=confidence,
    )


def _create_context(session: Session) -> tuple[UUID, UUID, UUID]:
    user = UserRepo(session).create(email="conv@example.com", name="Convergence")
    team = TeamRepo(session).create(name="convergence-team")
    project = ProjectRepo(session).create(team_id=team.id, name="conv-project", created_by=user.id)
    return team.id, project.id, user.id


def test_convergence_promotes_on_three_sut_agreement(engine: Any) -> None:
    factory = make_session_factory(engine)

    with factory() as session:
        team_id, project_id, user_id = _create_context(session)
        item = EvalItemRepo(session).create(
            team_id=team_id,
            suite="conv-suite",
            solution_id=None,
            item_input={"question": "x?"},
            gold_answer={"placeholder": True},
            tier=EvalItemTier.MODEL_PROPOSED,
            item_metadata={},
            question_hash="conv-qh-1",
            embedding=None,
        )
        for idx in range(3):
            _create_result_with_verdict(
                session,
                team_id=team_id,
                project_id=project_id,
                user_id=user_id,
                item_id=item.item_id,
                solution_label=f"sut-{idx}",
                answer_hash="gold-h1",
                canonical_answer={"rows": [[1]]},
            )
        session.commit()
        item_id = item.item_id

    worker = ConvergenceWorker(
        factory=factory,
        min_suts=3,
        min_confidence=0.85,
        batch_size=100,
    )
    worker.tick_for_team(team_id=team_id)

    with factory() as session:
        promoted = EvalItemRepo(session).get_active(item_id)
        assert promoted is not None
        assert promoted.tier == EvalItemTier.EXECUTION_CONFIRMED
        assert promoted.gold_answer == {"rows": [[1]]}

        events = ProvenanceRepo(session).list_for_item(item_id)
        promotion_events = [
            event for event in events if event.new_tier == EvalItemTier.EXECUTION_CONFIRMED
        ]
        assert len(promotion_events) == 1
        assert promotion_events[0].actor_type == ActorType.SYSTEM
        assert promotion_events[0].prior_tier == EvalItemTier.MODEL_PROPOSED

        state = WorkerStateRepo(session).get_or_create(
            worker_name="convergence",
            team_id=team_id,
        )
        assert state.tick_count == 1
        assert state.last_processed_id is not None


def test_convergence_no_promotion_with_disagreement(engine: Any) -> None:
    factory = make_session_factory(engine)

    with factory() as session:
        team_id, project_id, user_id = _create_context(session)
        item = EvalItemRepo(session).create(
            team_id=team_id,
            suite="conv-suite",
            solution_id=None,
            item_input={"question": "y?"},
            gold_answer={"placeholder": True},
            tier=EvalItemTier.MODEL_PROPOSED,
            item_metadata={},
            question_hash="conv-qh-2",
            embedding=None,
        )
        for idx, answer_hash in enumerate(["h-a", "h-a", "h-b"]):
            _create_result_with_verdict(
                session,
                team_id=team_id,
                project_id=project_id,
                user_id=user_id,
                item_id=item.item_id,
                solution_label=f"sut-{idx}",
                answer_hash=answer_hash,
                canonical_answer={"hash": answer_hash},
            )
        session.commit()
        item_id = item.item_id

    worker = ConvergenceWorker(
        factory=factory,
        min_suts=3,
        min_confidence=0.85,
        batch_size=100,
    )
    worker.tick_for_team(team_id=team_id)

    with factory() as session:
        item_after_tick = EvalItemRepo(session).get_active(item_id)
        assert item_after_tick is not None
        assert item_after_tick.tier == EvalItemTier.MODEL_PROPOSED


def test_convergence_idempotent_on_replay(engine: Any) -> None:
    factory = make_session_factory(engine)

    with factory() as session:
        team_id, project_id, user_id = _create_context(session)
        item = EvalItemRepo(session).create(
            team_id=team_id,
            suite="conv-suite",
            solution_id=None,
            item_input={"question": "z?"},
            gold_answer={"placeholder": True},
            tier=EvalItemTier.MODEL_PROPOSED,
            item_metadata={},
            question_hash="conv-qh-3",
            embedding=None,
        )
        for idx in range(3):
            _create_result_with_verdict(
                session,
                team_id=team_id,
                project_id=project_id,
                user_id=user_id,
                item_id=item.item_id,
                solution_label=f"sut-{idx}",
                answer_hash="gold-h1",
                canonical_answer={"rows": [[1]]},
            )
        session.commit()
        item_id = item.item_id

    worker = ConvergenceWorker(
        factory=factory,
        min_suts=3,
        min_confidence=0.85,
        batch_size=100,
    )
    worker.tick_for_team(team_id=team_id)

    with factory() as session:
        WorkerStateRepo(session).advance(
            worker_name="convergence",
            team_id=team_id,
            last_processed_id=None,
            last_processed_at=datetime.now(UTC),
        )
        session.commit()

    worker.tick_for_team(team_id=team_id)

    with factory() as session:
        events = ProvenanceRepo(session).list_for_item(item_id)
        promotion_events = [
            event for event in events if event.new_tier == EvalItemTier.EXECUTION_CONFIRMED
        ]
        assert len(promotion_events) == 1
