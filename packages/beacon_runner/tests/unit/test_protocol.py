from __future__ import annotations

from uuid import uuid4

import pytest
from beacon_runner.errors import SutNotFoundError
from beacon_runner.registry import SutRegistry
from beacon_runner.sut import SolutionUnderTest
from beacon_runner.types import (
    EvalItem,
    ExecutionResult,
    ExecutionStep,
    SolutionConfig,
    SolutionIdentity,
)


class _OkSut:
    def identity(self) -> SolutionIdentity:
        return SolutionIdentity(
            solution_id="ok",
            version="1",
            owner_team=uuid4(),
            summary="",
            supported_modes=["EVAL"],
            layers=[],
        )

    def layers(self) -> list[object]:
        return []

    def validate_config(self, config: SolutionConfig) -> list[str]:  # noqa: ARG002
        return []

    def invoke(self, item: EvalItem, config: SolutionConfig) -> ExecutionResult:  # noqa: ARG002
        return ExecutionResult(
            output={"x": 1},
            output_kind="json",
            trace=ExecutionStep(uuid="r", name="r", level="workflow", status="COMPLETED"),
        )


class _Incomplete:
    def identity(self) -> None:
        return None


def test_ok_sut_satisfies_protocol() -> None:
    assert isinstance(_OkSut(), SolutionUnderTest)


def test_incomplete_does_not_satisfy_protocol() -> None:
    assert not isinstance(_Incomplete(), SolutionUnderTest)


def test_registry_register_and_get() -> None:
    reg = SutRegistry()
    sut = _OkSut()
    reg.register(sut)
    got = reg.get("ok", "1")
    assert id(got) == id(sut)


def test_registry_raises_when_missing() -> None:
    reg = SutRegistry()
    with pytest.raises(SutNotFoundError):
        reg.get("nope", "v")


def test_registry_rejects_non_protocol_objects() -> None:
    reg = SutRegistry()
    with pytest.raises(TypeError):
        reg.register(_Incomplete())
