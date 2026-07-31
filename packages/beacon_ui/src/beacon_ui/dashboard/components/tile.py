"""KPI tile helpers."""

from __future__ import annotations

import streamlit as st


def kpi_tile(  # noqa: A002
    label: str,
    value: str,
    delta: str | None = None,
    help: str | None = None,
) -> None:
    """Render a labelled KPI tile via st.metric."""
    st.metric(label=label, value=value, delta=delta, help=help)


def format_pass_at_k(rate: float | None) -> str:
    """Format a pass@k rate as a percentage or 'n/a' when missing."""
    return f"{rate:.1%}" if isinstance(rate, int | float) else "n/a"


def format_delta(delta: float | None, *, sign: bool = True) -> str | None:
    """Format a delta as a signed percentage string, or None when missing."""
    if not isinstance(delta, int | float):
        return None
    sign_char = "+" if sign and delta >= 0 else ""
    return f"{sign_char}{delta:.1%}"
