"""End-to-end promotion-worker tick against real Postgres."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from beacon_storage.db import make_session_factory
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.provenance import ActorType
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.production_traces import ProductionTraceRepo
from beacon_storage.repository.provenance import ProvenanceRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.worker_state import WorkerStateRepo
from beacon_workers.promotion.judge import WellFormedJudge
from beacon_workers.promotion.worker import PromotionWorker

pytestmark = pytest.mark.integration


def _unit(*xs: float) -> np.ndarray:
    vector = np.array(xs, dtype=np.float32)
    return vector / (np.linalg.norm(vector) + 1e-12)


def _create_trace(
    repo: ProductionTraceRepo,
    *,
    team_id: Any,
    solution_id: str,
    question: str,
    output: dict[str, Any],
) -> Any:
    return repo.create(
        team_id=team_id,
        project_id=None,
        solution_id=solution_id,
        api_key_id=None,
        item_input={"question": question},
        item_output=output,
        trace_payload={},
        trace_metadata={},
        is_eval_candidate=True,
        derived_trace_id=None,
    )


def test_promotion_tick_extracts_and_inserts(engine: Any) -> None:
    factory = make_session_factory(engine)
    solution_id = "solution-a"

    with factory() as session:
        team = TeamRepo(session).create(name="promotion-team")
        trace_repo = ProductionTraceRepo(session)
        _create_trace(
            trace_repo,
            team_id=team.id,
            solution_id=solution_id,
            question="top 5 products by revenue in Q3?",
            output={"sql": "SELECT name FROM products LIMIT 5"},
        )
        _create_trace(
            trace_repo,
            team_id=team.id,
            solution_id=solution_id,
            question="customers who churned last month?",
            output={"sql": "SELECT id FROM customers WHERE churned"},
        )
        last_trace = _create_trace(
            trace_repo,
            team_id=team.id,
            solution_id=solution_id,
            question="how do I use this app?",
            output={},
        )
        session.commit()
        team_id = team.id
        last_trace_id = last_trace.id

    embedder = MagicMock()
    embedder.embed.return_value = [_unit(1, 0, 0), _unit(0, 1, 0), _unit(0, 0, 1)]

    judge_provider = MagicMock()

    def _judge(*, prompt: str, prompt_version: str) -> dict[str, Any]:
        if "top 5 products" in prompt:
            return {
                "score": 0.9,
                "reason": "clear DB question",
                "suggested_suite": "ad_hoc",
            }
        if "churned" in prompt:
            return {
                "score": 0.85,
                "reason": "well-formed",
                "suggested_suite": "ad_hoc",
            }
        return {"score": 0.2, "reason": "meta-question", "suggested_suite": None}

    judge_provider.judge_json.side_effect = _judge

    worker = PromotionWorker(
        factory=factory,
        embedder=embedder,
        judge=WellFormedJudge(provider=judge_provider, min_score=0.7),
        novelty_threshold=0.95,
        batch_size=10,
        max_per_team_per_tick=50,
    )

    worker.tick_for_team(team_id=team_id)

    with factory() as session:
        items = EvalItemRepo(session).list_active(
            team_id=team_id,
            tier=EvalItemTier.MODEL_PROPOSED,
        )
        assert len(items) == 2
        assert {item.item_metadata["suggested_suite"] for item in items} == {"ad_hoc"}

        provenance_events = ProvenanceRepo(session).list_for_team(team_id=team_id)
        assert len(provenance_events) == 2
        assert {event.actor_type for event in provenance_events} == {ActorType.SYSTEM}
        assert all("judge_score" in event.reason for event in provenance_events)

        state = WorkerStateRepo(session).get_or_create(
            worker_name="promotion",
            team_id=team_id,
        )
        assert state.last_processed_id == last_trace_id

    worker.tick_for_team(team_id=team_id)

    with factory() as session:
        items = EvalItemRepo(session).list_active(
            team_id=team_id,
            tier=EvalItemTier.MODEL_PROPOSED,
        )
        assert len(items) == 2


def test_promotion_tick_is_idempotent_on_replay(engine: Any) -> None:
    factory = make_session_factory(engine)
    solution_id = "solution-a"

    with factory() as session:
        team = TeamRepo(session).create(name="promotion-replay-team")
        _create_trace(
            ProductionTraceRepo(session),
            team_id=team.id,
            solution_id=solution_id,
            question="active accounts?",
            output={"sql": "SELECT count(*) FROM accounts"},
        )
        session.commit()
        team_id = team.id

    embedder = MagicMock()
    embedder.embed.return_value = [_unit(1, 0, 0)]

    judge_provider = MagicMock()
    judge_provider.judge_json.return_value = {
        "score": 0.9,
        "reason": "ok",
        "suggested_suite": "ad_hoc",
    }

    worker = PromotionWorker(
        factory=factory,
        embedder=embedder,
        judge=WellFormedJudge(provider=judge_provider, min_score=0.7),
        novelty_threshold=0.95,
        batch_size=10,
        max_per_team_per_tick=50,
    )

    worker.tick_for_team(team_id=team_id)

    with factory() as session:
        WorkerStateRepo(session).advance(
            worker_name="promotion",
            team_id=team_id,
            last_processed_id=None,
            last_processed_at=datetime.now(UTC),
        )
        session.commit()

    worker.tick_for_team(team_id=team_id)

    with factory() as session:
        items = EvalItemRepo(session).list_active(
            team_id=team_id,
            tier=EvalItemTier.MODEL_PROPOSED,
        )
        provenance_events = ProvenanceRepo(session).list_for_team(team_id=team_id)

        assert len(items) == 1
        assert len(provenance_events) == 1
