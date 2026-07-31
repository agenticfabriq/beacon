from unittest.mock import Mock, patch

import pytest
from beacon_ui.cli.ctx import Context
from beacon_ui.cli.http import CliHttpError, http_client_from_context


def test_client_includes_api_key() -> None:
    ctx = Context(api_base="http://x", api_key="bcn_xxx")
    with patch("beacon_ui.cli.http.httpx.Client") as mock_cls:
        mock_inst = Mock()
        mock_inst.get.return_value.status_code = 200
        mock_inst.get.return_value.json.return_value = {}
        mock_cls.return_value = mock_inst

        client = http_client_from_context(ctx)
        client.get("/v1/me")

        _, kwargs = mock_inst.get.call_args
        assert kwargs["headers"]["X-API-Key"] == "bcn_xxx"


def test_client_raises_on_4xx() -> None:
    ctx = Context(api_base="http://x", api_key="k")
    with patch("beacon_ui.cli.http.httpx.Client") as mock_cls:
        mock_inst = Mock()
        mock_inst.get.return_value.status_code = 403
        mock_inst.get.return_value.json.return_value = {"code": "x", "message": "no"}
        mock_cls.return_value = mock_inst

        client = http_client_from_context(ctx)
        with pytest.raises(CliHttpError):
            client.get("/v1/me")


def test_client_errors_when_unauthenticated() -> None:
    ctx = Context(api_base="http://x", api_key=None)
    with pytest.raises(CliHttpError):
        http_client_from_context(ctx)
