"""POST /v1/traces SDK ingestion endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from beacon_storage.repository.production_traces import ProductionTraceRepo

if TYPE_CHECKING:
    from beacon_storage.models.tenancy import Team
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_post_trace_with_valid_api_key(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
) -> None:
    payload = {
        "solution_id": "acme-chat-to-data",
        "project_id": None,
        "item_input": {"q": "?", "db": "x"},
        "item_output": {"sql": "SELECT 1", "answer": "1"},
        "trace": {"name": "root", "level": "workflow", "children": []},
        "metadata": {"latency_ms": 12, "cost_usd": 0.001},
        "is_eval_candidate": False,
    }

    response = api_client.post(
        "/v1/traces",
        json=payload,
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert "production_trace_id" in body
    assert body["derived_trace_id"] is None
    assert alice_team_membership.id


def test_post_trace_rejects_missing_api_key(api_client: TestClient) -> None:
    response = api_client.post(
        "/v1/traces",
        json={
            "solution_id": "x",
            "item_input": {},
            "item_output": {},
            "trace": {},
            "metadata": {},
        },
    )

    assert response.status_code == 401


def test_post_trace_rejects_invalid_payload(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
) -> None:
    response = api_client.post(
        "/v1/traces",
        json={
            "solution_id": "",
            "item_input": {},
            "item_output": {},
            "trace": {},
            "metadata": {},
        },
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 422
    assert alice_team_membership.id


def test_post_trace_records_is_eval_candidate(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
    session: Session,
) -> None:
    response = api_client.post(
        "/v1/traces",
        json={
            "solution_id": "x",
            "item_input": {"q": "?"},
            "item_output": {"a": "!"},
            "trace": {"name": "r", "level": "w", "children": []},
            "metadata": {},
            "is_eval_candidate": True,
        },
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 201, response.text
    production_trace_id = UUID(response.json()["production_trace_id"])
    session.expire_all()
    production_trace = ProductionTraceRepo(session).get(production_trace_id)
    assert production_trace is not None
    assert production_trace.is_eval_candidate is True
    assert production_trace.team_id == alice_team_membership.id
