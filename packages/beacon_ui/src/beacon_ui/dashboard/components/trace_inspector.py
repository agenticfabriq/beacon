"""Reusable trace inspectors for runs and review queues."""

from __future__ import annotations

import json
from typing import Any

import streamlit as st


def render_candidates_side_by_side(candidates: list[dict[str, Any]]) -> None:
    """Render up to three candidate outputs side by side for comparison."""
    if not candidates:
        st.info("No candidate outputs.")
        return

    cols = st.columns(min(len(candidates), 3))
    for col, candidate in zip(cols, candidates, strict=False):
        with col:
            status = str(candidate.get("pass_status") or "?").upper()
            st.markdown(f"**{candidate.get('solution_name', 'solution')}** - {status}")
            with st.expander("Output", expanded=True):
                st.code(json.dumps(candidate.get("output", {}), indent=2), language="json")
            with st.expander("Trace summary", expanded=False):
                trace_url = candidate.get("trace_url")
                if isinstance(trace_url, str) and trace_url:
                    st.link_button("Open full trace", trace_url)
                else:
                    st.caption("Trace URL not available.")


def render_single_trace(trace: dict[str, Any]) -> None:
    """Render a single trace tree with collapsible per-step input and output."""

    def _walk(step: dict[str, Any], depth: int = 0) -> None:
        name = step.get("name", "step")
        layer = step.get("layer") or "no-layer"
        with st.expander(f"{name} [{layer}]", expanded=depth == 0):
            if step.get("input"):
                st.caption("input")
                st.code(json.dumps(step["input"], indent=2), language="json")
            if step.get("output"):
                st.caption("output")
                st.code(json.dumps(step["output"], indent=2), language="json")
            children = step.get("children", [])
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        _walk(child, depth + 1)

    _walk(trace)
