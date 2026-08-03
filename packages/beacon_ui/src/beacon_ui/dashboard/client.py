"""HTTP client used by dashboard panels."""

from __future__ import annotations

from typing import Any, cast

import httpx


class BeaconApiError(Exception):
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
        raise BeaconApiError(response.status_code, code, message)
    return body


def _list_body(body: Any, *, key: str) -> list[dict[str, Any]]:
    value = body.get(key, []) if isinstance(body, dict) else body
    if not isinstance(value, list):
        return []
    return [cast("dict[str, Any]", item) for item in value if isinstance(item, dict)]


class BeaconApiClient:
    def __init__(self, *, base_url: str, api_key: str, timeout: float = 30.0) -> None:
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

    def _get(self, path: str, **params: Any) -> Any:
        return _raise_for_status(
            self._client.get(
                self._url(path),
                headers=self._headers(),
                params=self._params(params),
            )
        )

    def _post(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        return _raise_for_status(
            self._client.post(
                self._url(path),
                headers=self._headers(),
                json=body,
                params=self._params(params),
            )
        )

    def _patch(self, path: str, body: dict[str, Any]) -> Any:
        return _raise_for_status(
            self._client.patch(self._url(path), headers=self._headers(), json=body)
        )

    def _delete(self, path: str) -> Any:
        return _raise_for_status(self._client.delete(self._url(path), headers=self._headers()))

    def me(self) -> dict[str, Any]:
        """Return the authenticated user's profile and memberships."""
        return cast("dict[str, Any]", self._get("/v1/me"))

    def list_teams(self) -> list[dict[str, Any]]:
        """List teams visible to the authenticated user."""
        return _list_body(self._get("/v1/teams"), key="teams")

    def add_team_member(self, team_id: str, *, user_email: str, role: str) -> dict[str, Any]:
        """Add a user to a team in the requested role."""
        return cast(
            "dict[str, Any]",
            self._post(
                f"/v1/teams/{team_id}/members",
                {"user_email": user_email, "role": role},
            ),
        )

    def list_projects(self, *, team_id: str) -> list[dict[str, Any]]:
        """List projects in the given team."""
        return _list_body(self._get("/v1/projects", team_id=team_id), key="projects")

    def get_project(self, project_id: str) -> dict[str, Any]:
        """Return a project's metadata."""
        return cast("dict[str, Any]", self._get(f"/v1/projects/{project_id}"))

    def patch_project_settings(self, project_id: str, **body: Any) -> dict[str, Any]:
        """Update project settings, e.g. the pinned baseline run."""
        return cast(
            "dict[str, Any]",
            self._patch(f"/v1/projects/{project_id}/settings", body),
        )

    def list_project_solutions(self, project_id: str) -> list[dict[str, Any]]:
        """List solutions attached to a project."""
        return _list_body(self._get(f"/v1/projects/{project_id}/solutions"), key="solutions")

    def add_project_solution(self, project_id: str, *, solution_id: str) -> dict[str, Any]:
        """Attach a team-catalog solution to a project."""
        return cast(
            "dict[str, Any]",
            self._post(
                f"/v1/projects/{project_id}/solutions",
                {"solution_id": solution_id},
            ),
        )

    def remove_project_solution(self, project_id: str, solution_id: str) -> None:
        """Detach a solution from a project."""
        self._delete(f"/v1/projects/{project_id}/solutions/{solution_id}")

    def list_project_suites(self, project_id: str) -> list[dict[str, Any]]:
        """List suites defined in a project."""
        return _list_body(self._get(f"/v1/projects/{project_id}/suites"), key="suites")

    def create_suite(self, project_id: str, **body: Any) -> dict[str, Any]:
        """Create a suite in a project."""
        return cast("dict[str, Any]", self._post(f"/v1/projects/{project_id}/suites", body))

    def list_runs(self, project_id: str, **filters: Any) -> list[dict[str, Any]]:
        """List runs in a project, optionally filtered."""
        return _list_body(self._get(f"/v1/projects/{project_id}/runs", **filters), key="runs")

    def get_run(self, project_id: str, run_id: str) -> dict[str, Any]:
        """Return a run's status and summary metrics."""
        return cast("dict[str, Any]", self._get(f"/v1/projects/{project_id}/runs/{run_id}"))

    def register_run(self, project_id: str, **body: Any) -> dict[str, Any]:
        """Register a run for a runner to execute elsewhere and push results to.

        Renamed from ``kick_off_run``: beacon does not execute anything, and the
        old name promised a queue that never existed.
        """
        return cast("dict[str, Any]", self._post(f"/v1/projects/{project_id}/runs", body))

    def list_results(self, project_id: str, run_id: str, **filters: Any) -> dict[str, Any]:
        """List a run's per-item results with facet counts."""
        return cast(
            "dict[str, Any]",
            self._get(f"/v1/projects/{project_id}/runs/{run_id}/results", **filters),
        )

    def get_result(self, project_id: str, run_id: str, item_id: str) -> dict[str, Any]:
        """Return one item's answer beside the gold it was graded against."""
        return cast(
            "dict[str, Any]",
            self._get(f"/v1/projects/{project_id}/runs/{run_id}/results/{item_id}"),
        )

    def invalidate_run(self, project_id: str, run_id: str, *, reason: str) -> dict[str, Any]:
        """Retire a run without deleting it. The reason is required."""
        return cast(
            "dict[str, Any]",
            self._post(f"/v1/projects/{project_id}/runs/{run_id}/invalidate", {"reason": reason}),
        )

    def restore_run(self, project_id: str, run_id: str) -> dict[str, Any]:
        """Undo an invalidation."""
        return cast(
            "dict[str, Any]",
            self._post(f"/v1/projects/{project_id}/runs/{run_id}/restore", {}),
        )

    def list_team_members(self, team_id: str) -> list[dict[str, Any]]:
        """Return the team roster."""
        return _list_body(self._get(f"/v1/teams/{team_id}/members"), key="members")

    def remove_team_member(self, team_id: str, user_id: str) -> None:
        """Revoke a team membership."""
        self._delete(f"/v1/teams/{team_id}/members/{user_id}")

    def get_attribution(self, project_id: str, *, sut: str, suite: str) -> dict[str, Any]:
        """Return the latest attribution snapshot for a solution and suite."""
        return cast(
            "dict[str, Any]",
            self._get(f"/v1/projects/{project_id}/attribution", sut=sut, suite=suite),
        )

    def cost_leaderboard(self, *, suite: str) -> dict[str, Any]:
        """Return the cost-adjusted leaderboard for a shared suite."""
        return cast("dict[str, Any]", self._get("/v1/leaderboards/cost", suite=suite))

    def latency_leaderboard(self, *, suite: str) -> dict[str, Any]:
        """Return the latency-adjusted leaderboard for a shared suite."""
        return cast("dict[str, Any]", self._get("/v1/leaderboards/latency", suite=suite))
