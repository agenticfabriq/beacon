"""Settings: configuration, not measurement.

Nothing here answers a question about how a system is doing, which is why none
of it is in the navigation. Team, project, systems under test, access and API
keys all live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiClient, BeaconApiError
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def _workspace(client: BeaconApiClient, state: DashboardState) -> None:
    """Team and project selection, demoted from navigation to configuration."""
    st.subheader("Workspace")
    try:
        teams = client.list_teams()
    except BeaconApiError as exc:
        st.error(exc.message)
        return
    team_ids = [str(team["id"]) for team in teams if "id" in team]
    if not team_ids:
        st.caption("No teams visible.")
        return
    team_id = st.selectbox(
        "Team",
        options=team_ids,
        index=team_ids.index(state.current_team_id) if state.current_team_id in team_ids else 0,
        format_func=lambda value: next(
            str(team["name"]) for team in teams if str(team["id"]) == value
        ),
        key="settings_team_select",
    )
    if team_id != state.current_team_id:
        state.select_team(team_id)
        st.rerun()

    try:
        projects = client.list_projects(team_id=team_id)
    except BeaconApiError:
        projects = []
    project_ids = [str(project["id"]) for project in projects if "id" in project]
    if not project_ids:
        st.caption("No projects in this team.")
        return
    project_id = st.selectbox(
        "Project",
        options=project_ids,
        index=(
            project_ids.index(state.current_project_id)
            if state.current_project_id in project_ids
            else 0
        ),
        format_func=lambda value: next(
            str(project["name"]) for project in projects if str(project["id"]) == value
        ),
        key="settings_project_select",
    )
    if project_id != state.current_project_id:
        state.select_project(project_id)
        st.rerun()


def _benchmark(client: BeaconApiClient, state: DashboardState) -> None:
    st.subheader("This benchmark")
    if state.current_project_id is None:
        return
    try:
        projects = client.list_projects(team_id=state.current_team_id or "")
        suites = client.list_project_suites(state.current_project_id)
    except BeaconApiError as exc:
        st.error(exc.message)
        return
    suite = next((item for item in suites if str(item.get("id")) == state.current_suite_id), None)
    if suite is not None:
        st.caption(f"{suite.get('name')} · {suite.get('item_count')} questions")
    project = next(
        (item for item in projects if str(item.get("id")) == state.current_project_id), None
    )
    baseline = (project or {}).get("baseline_run_id")
    st.caption(
        f"Reference run: {str(baseline)[:8] if baseline else 'not set'} — "
        "pin one from the Runs page, where you can see what you are pinning."
    )


def _systems(client: BeaconApiClient, state: DashboardState) -> None:
    st.subheader("Systems under test")
    st.caption(
        "Registered by the runner when it registers a run: the first declaration "
        "of a (system, version) creates it, an identical one is reused, and a "
        "divergent one is refused. This table lists; it does not author."
    )
    if state.current_team_id is None:
        return
    try:
        solutions = client.list_team_solutions(state.current_team_id)
    except BeaconApiError as exc:
        st.error(exc.message)
        return
    if not solutions:
        st.caption("Nothing registered yet.")
        return
    st.dataframe(
        [
            {
                "system": row.get("solution_id"),
                "version": row.get("version"),
                "layers": ", ".join(
                    str(layer.get("name"))
                    for layer in row.get("layers", [])
                    if isinstance(layer, dict)
                ),
            }
            for row in solutions
        ],
        width="stretch",
        hide_index=True,
    )


def _access(client: BeaconApiClient, state: DashboardState) -> None:
    st.subheader("Access")
    st.caption("Team is the isolation boundary: who can see this data. Not a hierarchy level.")
    if state.current_team_id is None:
        return
    try:
        members = client.list_team_members(state.current_team_id)
    except BeaconApiError as exc:
        st.error(exc.message)
        return
    if members:
        st.dataframe(
            [
                {"email": m.get("email"), "name": m.get("name"), "role": m.get("role")}
                for m in members
            ],
            width="stretch",
            hide_index=True,
        )
    columns = st.columns(3)
    with columns[0]:
        email = st.text_input("Invite by email", key="settings_invite_email")
    with columns[1]:
        role = st.selectbox(
            "Role", options=["team_member", "team_admin"], key="settings_invite_role"
        )
    with columns[2]:
        if st.button("Add member", key="settings_invite_btn", disabled=not email.strip()):
            try:
                client.add_team_member(state.current_team_id, user_email=email.strip(), role=role)
            except BeaconApiError as exc:
                st.error(exc.message)
                return
            st.rerun()
    removable = [m for m in members if isinstance(m.get("user_id"), str)]
    if removable:
        target = st.selectbox(
            "Remove a member",
            options=[str(m["user_id"]) for m in removable],
            format_func=lambda uid: next(
                str(m.get("email")) for m in removable if str(m["user_id"]) == uid
            ),
            key="settings_remove_select",
        )
        if st.button("Remove", key="settings_remove_btn"):
            try:
                client.remove_team_member(state.current_team_id, target)
            except BeaconApiError as exc:
                # Removing the last admin is refused so the team stays recoverable.
                st.error(exc.message)
                return
            st.rerun()


def _api_keys(client: BeaconApiClient) -> None:
    st.subheader("API keys")
    st.caption("What a runner sends when it pushes results. Shown once; stored as a hash.")
    revealed = st.session_state.get("_settings_new_key")
    if isinstance(revealed, str) and revealed:
        st.warning(
            "Copy this now — beacon cannot show it again. "
            "If you lose it, revoke and create another."
        )
        st.code(revealed)
        if st.button("Done", key="settings_key_dismiss"):
            st.session_state["_settings_new_key"] = None
            st.rerun()
    columns = st.columns(2)
    with columns[0]:
        label = st.text_input("Label, e.g. mnemiq-ci", key="settings_key_label")
    with columns[1]:
        if st.button("Create key", key="settings_key_create", disabled=not label.strip()):
            try:
                created = client.create_api_key(label=label.strip())
            except BeaconApiError as exc:
                st.error(exc.message)
                return
            st.session_state["_settings_new_key"] = created.get("api_key")
            st.rerun()
    try:
        keys = client.list_api_keys()
    except BeaconApiError as exc:
        st.error(exc.message)
        return
    if not keys:
        st.caption("No keys. Create one for your runner.")
        return
    st.dataframe(
        [
            {
                "label": key.get("label"),
                "created": key.get("created_at"),
                "last used": key.get("last_used_at") or "never",
            }
            for key in keys
        ],
        width="stretch",
        hide_index=True,
    )
    target = st.selectbox(
        "Revoke a key",
        options=[str(key["id"]) for key in keys],
        format_func=lambda kid: next(str(k.get("label")) for k in keys if str(k["id"]) == kid),
        key="settings_key_revoke_select",
    )
    if st.button("Revoke", key="settings_key_revoke_btn"):
        try:
            client.revoke_api_key(target)
        except BeaconApiError as exc:
            st.error(exc.message)
            return
        st.rerun()


def _archive(client: BeaconApiClient, state: DashboardState) -> None:
    st.subheader("Archive")
    st.caption("Archived projects are hidden from active project lists.")
    if state.current_project_id is None:
        return
    if st.button("Archive project", type="secondary", key="settings_archive_btn"):
        try:
            client._delete(f"/v1/projects/{state.current_project_id}")  # noqa: SLF001
        except BeaconApiError as exc:
            st.error(exc.message)
            return
        state.select_project(None)
        st.rerun()


def render() -> None:
    """Render every configuration section."""
    state = _state()
    client = client_from_state()
    _workspace(client, state)
    st.divider()
    _benchmark(client, state)
    st.divider()
    _systems(client, state)
    st.divider()
    _access(client, state)
    st.divider()
    _api_keys(client)
    st.divider()
    _archive(client, state)


if __name__ == "__main__":  # pragma: no cover
    render()
