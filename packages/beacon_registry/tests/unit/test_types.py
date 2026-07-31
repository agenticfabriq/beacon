"""beacon_registry.types: request/response models and storage enum re-exports."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from beacon_registry.types import (
    ActorType,
    EvalItemSummary,
    EvalItemTier,
    PromoteRequest,
    TraceIngestRequest,
    TraceIngestResult,
)
from pydantic import ValidationError


def test_promote_request_minimal() -> None:
    request = PromoteRequest(
        new_tier=EvalItemTier.EXECUTION_CONFIRMED,
        reason="3 SUTs converged",
    )

    assert request.new_tier == EvalItemTier.EXECUTION_CONFIRMED
    assert request.evidence == {}


def test_promote_request_rejects_empty_reason() -> None:
    with pytest.raises(ValidationError):
        PromoteRequest(new_tier=EvalItemTier.HUMAN_VERIFIED, reason="")


def test_eval_item_summary_round_trip() -> None:
    summary = EvalItemSummary(
        item_id=uuid4(),
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="bird",
        dataset_version="v",
        team_id=None,
        valid_from="2026-06-05T00:00:00+00:00",
        item_input_preview="how many?",
    )

    assert summary.suite == "bird"


def test_trace_ingest_request_defaults() -> None:
    request = TraceIngestRequest(
        solution_id="acme-chat-to-data",
        item_input={"q": "?"},
        item_output={"a": "!"},
        trace={"name": "root", "level": "workflow", "children": []},
    )

    assert request.project_id is None
    assert request.metadata == {}
    assert request.is_eval_candidate is False


def test_trace_ingest_request_requires_solution_id() -> None:
    with pytest.raises(ValidationError):
        TraceIngestRequest(
            solution_id="",
            item_input={"q": "?"},
            item_output={"a": "!"},
            trace={"name": "root", "level": "workflow", "children": []},
            metadata={},
        )


def test_trace_ingest_result_round_trip() -> None:
    production_trace_id = uuid4()
    created_at = datetime.fromisoformat("2026-06-05T00:00:00+00:00")
    result = TraceIngestResult(
        production_trace_id=production_trace_id,
        created_at=created_at,
    )

    assert result.production_trace_id == production_trace_id
    assert result.derived_trace_id is None


def test_actor_type_enum_values() -> None:
    assert ActorType.HUMAN.value == "human"
    assert ActorType.SYSTEM.value == "system"
    assert ActorType.LLM_JUDGE.value == "llm_judge"
