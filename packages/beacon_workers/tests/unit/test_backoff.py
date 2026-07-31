"""Backoff policies for transient and fatal worker errors."""

from __future__ import annotations

import pytest
from beacon_workers.backoff import FatalWorkerError, TransientWorkerError, retry_transient
from sqlalchemy.exc import OperationalError


def test_retry_transient_eventually_succeeds() -> None:
    calls = {"n": 0}

    @retry_transient(max_attempts=3, initial_wait_seconds=0.01)
    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientWorkerError("temp")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_does_not_swallow_fatal() -> None:
    @retry_transient(max_attempts=3, initial_wait_seconds=0.01)
    def boom() -> None:
        raise FatalWorkerError("permanent")

    with pytest.raises(FatalWorkerError):
        boom()


def test_sqlalchemy_operational_treated_as_transient() -> None:
    calls = {"n": 0}

    @retry_transient(max_attempts=3, initial_wait_seconds=0.01)
    def db_flaky() -> int:
        calls["n"] += 1
        if calls["n"] < 2:
            raise OperationalError("stmt", {}, Exception("conn lost"))
        return 42

    assert db_flaky() == 42


def test_exhaustion_reraises_transient() -> None:
    @retry_transient(max_attempts=2, initial_wait_seconds=0.01)
    def always_fail() -> None:
        raise TransientWorkerError("nope")

    with pytest.raises(TransientWorkerError):
        always_fail()
