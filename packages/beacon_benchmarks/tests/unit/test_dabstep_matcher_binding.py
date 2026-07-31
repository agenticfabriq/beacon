"""DABStep grader binding tests."""

from __future__ import annotations

from typing import Any

from beacon_benchmarks.dabstep.adapter import DabstepAdapter


def test_adapter_metadata() -> None:
    metadata = DabstepAdapter.metadata

    assert metadata.suite == "dabstep_v1"
    # No BEACON_MIRROR_BASE_URL in the test environment, so no mirror is wired.
    assert metadata.internal_mirror is None


def test_register_graders_returns_matcher_bound_to_suite() -> None:
    class FakeRegistry:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def register(self, grader: Any) -> None:
            self.added.append(grader)

    registry = FakeRegistry()
    out = DabstepAdapter().register_graders(registry)

    assert len(out) == 1
    grader = out[0]
    assert grader.suite_filter == "dabstep_v1"
    assert grader.handle_not_applicable is True
    assert registry.added == out
