"""HTTP client used by CLI commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from beacon_ui.cli.ctx import Context


class CliHttpError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


def _response_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def _raise_for_status(response: httpx.Response) -> Any:
    body = _response_json(response)
    if response.status_code >= 400:
        if isinstance(body, dict):
            detail = body.get("detail")
            code = str(body.get("code") or "error")
            message = str(body.get("message") or detail or response.text)
        else:
            code = "error"
            message = response.text
        raise CliHttpError(response.status_code, code, message)
    return body


class CliHttpClient:
    def __init__(self, *, base_url: str, api_key: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _params(self, params: dict[str, Any]) -> dict[str, Any] | None:
        filtered = {key: value for key, value in params.items() if value is not None}
        return filtered or None

    def get(self, path: str, **params: Any) -> Any:
        """Send an authenticated GET and return the parsed JSON body."""
        return _raise_for_status(
            self._client.get(
                self._url(path),
                headers=self._headers(),
                params=self._params(params),
            )
        )

    def post(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        """Send an authenticated POST with a JSON body and return the response."""
        return _raise_for_status(
            self._client.post(
                self._url(path),
                headers=self._headers(),
                json=body,
                params=self._params(params),
            )
        )

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        """Send an authenticated PATCH with a JSON body and return the response."""
        return _raise_for_status(
            self._client.patch(self._url(path), headers=self._headers(), json=body)
        )

    def delete(self, path: str) -> Any:
        """Send an authenticated DELETE and return the response body if any."""
        return _raise_for_status(self._client.delete(self._url(path), headers=self._headers()))


def http_client_from_context(ctx: Context) -> CliHttpClient:
    """Build a CliHttpClient from a saved context, requiring an API key."""
    if not ctx.api_key:
        raise CliHttpError(401, "not_authenticated", "Run `beacon login` first.")
    return CliHttpClient(base_url=ctx.api_base, api_key=ctx.api_key)
