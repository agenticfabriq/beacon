"""Shared fixtures for grader tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture
def empty_trace() -> ExecutionStep:
    return ExecutionStep(uuid="r", name="root", level="workflow", status="COMPLETED")


@pytest.fixture
def make_result(empty_trace: ExecutionStep) -> Callable[..., ExecutionResult]:
    def _make(output: dict[str, Any], output_kind: str = "json", **kwargs: Any) -> ExecutionResult:
        return ExecutionResult(output=output, output_kind=output_kind, trace=empty_trace, **kwargs)

    return _make


@pytest.fixture
def make_item() -> Callable[..., EvalItem]:
    def _make(
        *,
        item_id: str = "i-1",
        suite: str = "test_suite",
        query: dict[str, Any] | None = None,
        ground_truth: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvalItem:
        return EvalItem(
            item_id=item_id,
            suite=suite,
            query=query or {"question": "?"},
            ground_truth=ground_truth or {},
            metadata=metadata or {},
        )

    return _make
