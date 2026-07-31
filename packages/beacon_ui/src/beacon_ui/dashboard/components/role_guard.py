"""Client-side role checks for dashboard affordances."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import streamlit as st

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


def has_role_in(
    memberships: Iterable[dict[str, Any]],
    *,
    scope_kind: str,
    scope_id: str,
    roles: Sequence[str],
) -> bool:
    """Return True when the memberships grant any of the roles on the target scope."""
    for membership in memberships:
        if membership.get("scope_kind") == "global" and membership.get("role") == "beacon_admin":
            return True
        if membership.get("scope_kind") != scope_kind:
            continue
        if str(membership.get("scope_id")) != str(scope_id):
            continue
        if membership.get("role") in roles:
            return True
    return False


def render_403(message: str = "You do not have permission to view this section.") -> None:
    """Render a 403 warning banner in place of restricted content."""
    st.warning(message)
