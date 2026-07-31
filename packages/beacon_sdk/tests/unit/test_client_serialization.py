"""BeaconClient serialization helpers."""

from __future__ import annotations

from uuid import uuid4

import pytest
from beacon_sdk.client import BeaconClient


def test_build_payload_includes_required_fields() -> None:
    payload = BeaconClient._build_payload(
        solution_id="x",
        project_id=None,
        item_input={"q": "?"},
        item_output={"a": "!"},
        trace={"name": "r", "level": "w", "children": []},
        metadata={"latency_ms": 10},
        is_eval_candidate=False,
    )

    assert payload["solution_id"] == "x"
    assert payload["project_id"] is None
    assert payload["item_input"] == {"q": "?"}
    assert payload["item_output"] == {"a": "!"}
    assert payload["trace"] == {"name": "r", "level": "w", "children": []}
    assert payload["metadata"] == {"latency_ms": 10}
    assert payload["is_eval_candidate"] is False


def test_build_payload_serializes_uuid_project_id() -> None:
    project_id = uuid4()

    payload = BeaconClient._build_payload(
        solution_id="x",
        project_id=project_id,
        item_input={},
        item_output={},
        trace={},
        metadata={},
        is_eval_candidate=False,
    )

    assert payload["project_id"] == str(project_id)


def test_build_payload_validates_solution_id_non_empty() -> None:
    with pytest.raises(ValueError):
        BeaconClient._build_payload(
            solution_id="",
            project_id=None,
            item_input={},
            item_output={},
            trace={},
            metadata={},
            is_eval_candidate=False,
        )


def test_default_headers_include_api_key() -> None:
    headers = BeaconClient._default_headers(api_key="bcn_dev_xyz")

    assert headers["X-API-Key"] == "bcn_dev_xyz"
    assert headers["Content-Type"] == "application/json"


def test_default_headers_omit_api_key_when_none() -> None:
    headers = BeaconClient._default_headers(api_key=None)

    assert "X-API-Key" not in headers
