"""Formatting helpers shared by dashboard panels."""

from __future__ import annotations


def format_pass_at_k(value: float | None) -> str:
    """Render a pass rate. None is unknown, not zero, and must not read as 0%."""
    return f"{value:.1%}" if value is not None else "n/a"
