"""Every adapter-registered grader must be classifiable by the composer (B5).

Adapters rename their grader instances per suite (``bird_minidev_v2.exec_sql``,
``spider2_lite.exec_sql``, …). Composition precedence used to key on those
names against a frozen set, so real execution verdicts silently composed ERROR.
This census keeps the two halves from drifting apart again.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
import sqlalchemy as sa
from beacon_benchmarks.bird_minidev.adapter import BirdMinidevAdapter
from beacon_graders.llm.provider import JudgeCache, JudgeRequest, JudgeResponse
from beacon_graders.types import GraderKind

_ADAPTERS = [
    BirdMinidevAdapter,
]


class _StubProvider:
    name = "stub"
    model_version = "stub-1"

    def generate(self, request: JudgeRequest) -> JudgeResponse:  # noqa: ARG002
        return JudgeResponse(
            text="{}",
            tokens_input=0,
            tokens_output=0,
            model_version=self.model_version,
            raw={},
        )


class _StubRegistry:
    """Captures what an adapter registers, standing in for the real registry."""

    def __init__(self) -> None:
        self.graders: list[Any] = []
        self.engine = sa.create_engine("sqlite+pysqlite:///:memory:")
        self.judge_cache = JudgeCache(provider=_StubProvider())

    def engine_factory(self, _item: Any) -> Any:
        return self.engine

    def register(self, grader: Any) -> None:
        self.graders.append(grader)


@pytest.mark.parametrize("adapter_type", _ADAPTERS, ids=lambda t: t.__name__)
def test_every_registered_grader_declares_a_kind(adapter_type: type[Any]) -> None:
    """A grader the composer cannot classify falls through to ERROR."""
    registry = _StubRegistry()
    graders = adapter_type().register_graders(registry)

    assert graders, f"{adapter_type.__name__} registered no graders"
    for grader in graders:
        kind = getattr(grader, "kind", None)
        assert isinstance(kind, GraderKind), (
            f"{adapter_type.__name__} registered {grader.name!r} with no GraderKind; "
            "the composer would classify its verdicts as ERROR"
        )


@pytest.mark.parametrize("adapter_type", _ADAPTERS, ids=lambda t: t.__name__)
def test_renaming_does_not_change_the_declared_kind(adapter_type: type[Any]) -> None:
    """The rename is a display label; classification must survive it."""
    registry = _StubRegistry()
    graders = adapter_type().register_graders(registry)

    renamed = [grader for grader in graders if grader.name != type(grader).name]
    for grader in renamed:
        assert grader.kind is type(grader).kind


@pytest.mark.parametrize("adapter_type", _ADAPTERS, ids=lambda t: t.__name__)
def test_every_registered_grader_emits_a_list_of_verdicts(adapter_type: type[Any]) -> None:
    """``compose`` does ``verdicts.extend(grader.grade(...))``.

    A grader returning a bare object raises there and the item composes ERROR.
    ``isinstance(grader, Grader)`` does **not** catch this: ``runtime_checkable``
    checks attribute presence only, never signatures or return types.
    """
    registry = _StubRegistry()
    for grader in adapter_type().register_graders(registry):
        annotation = inspect.signature(type(grader).grade).return_annotation
        assert "list" in str(annotation), (
            f"{adapter_type.__name__} registered {grader.name!r} whose grade() "
            f"returns {annotation!r}; VerdictComposer.extend would raise on it"
        )
