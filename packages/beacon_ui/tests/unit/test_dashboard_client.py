from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest
from beacon_ui.dashboard.client import BeaconApiClient, BeaconApiError


def test_client_sends_api_key_header() -> None:
    with patch("beacon_ui.dashboard.client.httpx.Client") as mock_cls:
        mock_client = Mock()
        mock_client.get.return_value.status_code = 200
        mock_client.get.return_value.json.return_value = {"user": {"id": "u"}}
        mock_cls.return_value = mock_client

        client = BeaconApiClient(base_url="http://api", api_key="bcn_dev_xyz")
        client.me()

        _, kwargs = mock_client.get.call_args
        headers = kwargs["headers"]
        assert headers["X-API-Key"] == "bcn_dev_xyz"


def test_client_raises_on_403() -> None:
    with patch("beacon_ui.dashboard.client.httpx.Client") as mock_cls:
        mock_client = Mock()
        mock_client.get.return_value.status_code = 403
        mock_client.get.return_value.text = "forbidden"
        mock_client.get.return_value.json.return_value = {
            "code": "not_authorized",
            "message": "no perm",
        }
        mock_cls.return_value = mock_client

        client = BeaconApiClient(base_url="http://api", api_key="x")
        with pytest.raises(BeaconApiError) as exc:
            client.list_runs(project_id="p")

        assert exc.value.status == 403
        assert exc.value.code == "not_authorized"
        assert exc.value.message == "no perm"


def test_list_review_queue_passes_filters() -> None:
    with patch("beacon_ui.dashboard.client.httpx.Client") as mock_cls:
        mock_client = Mock()
        mock_client.get.return_value.status_code = 200
        mock_client.get.return_value.json.return_value = {
            "items": [],
            "total": 0,
            "limit": 20,
            "offset": 0,
        }
        mock_cls.return_value = mock_client

        client = BeaconApiClient(base_url="http://api", api_key="x")
        client.list_review_queue(project_id="p1", suite="bird", limit=10)

        _, kwargs = mock_client.get.call_args
        params: dict[str, Any] = kwargs["params"]
        assert params["suite"] == "bird"
        assert params["limit"] == 10


def test_list_projects_uses_team_id_param() -> None:
    with patch("beacon_ui.dashboard.client.httpx.Client") as mock_cls:
        mock_client = Mock()
        mock_client.get.return_value.status_code = 200
        mock_client.get.return_value.json.return_value = []
        mock_cls.return_value = mock_client

        client = BeaconApiClient(base_url="http://api/", api_key="x")
        client.list_projects(team_id="t1")

        url, kwargs = mock_client.get.call_args.args[0], mock_client.get.call_args.kwargs
        assert url == "http://api/v1/projects"
        assert kwargs["params"] == {"team_id": "t1"}
