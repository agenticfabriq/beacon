"""Shared dashboard components."""

from beacon_ui.dashboard.components.role_guard import has_role_in, render_403
from beacon_ui.dashboard.components.tile import format_delta, format_pass_at_k, kpi_tile
from beacon_ui.dashboard.components.top_nav import render_top_nav
from beacon_ui.dashboard.components.trace_inspector import (
    render_candidates_side_by_side,
    render_single_trace,
)

__all__ = [
    "format_delta",
    "format_pass_at_k",
    "has_role_in",
    "kpi_tile",
    "render_403",
    "render_candidates_side_by_side",
    "render_single_trace",
    "render_top_nav",
]
