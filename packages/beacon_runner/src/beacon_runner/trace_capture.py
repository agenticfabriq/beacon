"""Context-manager-driven ExecutionStep tree assembly."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from beacon_storage.ids import uuid7_str

from beacon_runner.types import ExecutionStep

if TYPE_CHECKING:
    from collections.abc import Iterator

StepStatus = Literal["COMPLETED", "FAILED", "SKIPPED", "RUNNING"]


@dataclass
class _MutableStep:
    uuid: str
    name: str
    level: str
    status: StepStatus = "RUNNING"
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    children: list[_MutableStep] = field(default_factory=list)

    def mark_skipped(self, reason: str) -> None:
        """Mark this step ``SKIPPED`` and record ``reason`` in metadata."""
        self.status = "SKIPPED"
        self.metadata["skip_reason"] = reason

    def freeze(self) -> ExecutionStep:
        """Convert this mutable node and its children to an immutable ``ExecutionStep``."""
        return ExecutionStep(
            uuid=self.uuid,
            name=self.name,
            level=self.level,
            status=self.status,
            inputs=self.inputs,
            outputs=self.outputs,
            metadata=self.metadata,
            error=self.error,
            children=[child.freeze() for child in self.children],
        )


class TraceBuilder:
    """Build an ExecutionStep tree via nested context managers."""

    def __init__(self) -> None:
        self._stack: list[_MutableStep] = []
        self._root: _MutableStep | None = None

    @contextmanager
    def step(self, *, name: str, level: str) -> Iterator[_MutableStep]:
        """Open a nested step that auto-completes or fails on context exit."""
        node = _MutableStep(uuid=uuid7_str(), name=name, level=level)
        if self._stack:
            self._stack[-1].children.append(node)
        else:
            if self._root is not None:
                raise RuntimeError("TraceBuilder already has a root; nest under it.")
            self._root = node

        self._stack.append(node)
        try:
            yield node
        except Exception as exc:
            node.status = "FAILED"
            node.error = str(exc)[:1000]
            for ancestor in self._stack[:-1]:
                if ancestor.status != "FAILED":
                    ancestor.status = "FAILED"
            raise
        finally:
            if node.status == "RUNNING":
                node.status = "COMPLETED"
            self._stack.pop()

    def build(self) -> ExecutionStep:
        """Freeze the recorded tree into an immutable root ``ExecutionStep``."""
        if self._root is None:
            raise RuntimeError("TraceBuilder: no root step recorded")
        return self._root.freeze()
