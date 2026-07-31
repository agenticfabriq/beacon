"""Beacon runner: SUT protocol, harness, DummySUT, CLI."""

from beacon_runner.dummy_sut import DummySUT
from beacon_runner.errors import (
    BeaconRunnerError,
    HarnessModeNotSupportedError,
    SutInvocationError,
    SutNotFoundError,
    SutTimeoutError,
    SutValidationError,
)
from beacon_runner.registry import SutRegistry, default_registry
from beacon_runner.sut import SolutionUnderTest
from beacon_runner.trace_capture import TraceBuilder
from beacon_runner.types import (
    EvalItem,
    ExecutionResult,
    ExecutionStep,
    Instrumentation,
    Layer,
    SolutionConfig,
    SolutionIdentity,
)

__all__ = [
    "BeaconRunnerError",
    "DummySUT",
    "EvalItem",
    "ExecutionResult",
    "ExecutionStep",
    "HarnessModeNotSupportedError",
    "Instrumentation",
    "Layer",
    "SolutionConfig",
    "SolutionIdentity",
    "SolutionUnderTest",
    "SutRegistry",
    "SutInvocationError",
    "SutNotFoundError",
    "SutTimeoutError",
    "SutValidationError",
    "TraceBuilder",
    "default_registry",
]
