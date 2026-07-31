from __future__ import annotations

from uuid import UUID

import pytest
from beacon_runner.trace_capture import TraceBuilder
from beacon_runner.types import ExecutionStep


def test_builder_yields_root_step_when_no_nesting() -> None:
    builder = TraceBuilder()
    with builder.step(name="root", level="workflow") as step:
        step.outputs["x"] = 1

    root = builder.build()

    assert isinstance(root, ExecutionStep)
    assert UUID(root.uuid).version == 7
    assert root.name == "root"
    assert root.outputs == {"x": 1}
    assert root.status == "COMPLETED"


def test_nested_steps_form_a_tree() -> None:
    builder = TraceBuilder()
    with builder.step(name="root", level="workflow"):
        with builder.step(name="a", level="layer:ontology"):
            pass
        with builder.step(name="b", level="step"), builder.step(name="b1", level="step"):
            pass

    root = builder.build()

    assert [child.name for child in root.children] == ["a", "b"]
    assert root.children[1].children[0].name == "b1"


def test_exception_inside_step_marks_failed_and_propagates() -> None:
    builder = TraceBuilder()
    with (
        pytest.raises(RuntimeError, match="boom"),
        builder.step(name="root", level="workflow"),
        builder.step(name="oops", level="step"),
    ):
        raise RuntimeError("boom")

    root = builder.build()

    assert root.children[0].status == "FAILED"
    assert root.children[0].error == "boom"
    assert root.status == "FAILED"


def test_skipped_step_via_mark_skipped() -> None:
    builder = TraceBuilder()
    with (
        builder.step(name="root", level="workflow"),
        builder.step(name="ont", level="layer:ontology") as step,
    ):
        step.mark_skipped("disabled by config")

    root = builder.build()

    assert root.children[0].status == "SKIPPED"
    assert root.children[0].metadata.get("skip_reason") == "disabled by config"


def test_build_without_root_step_raises() -> None:
    builder = TraceBuilder()
    with pytest.raises(RuntimeError, match="no root"):
        builder.build()
