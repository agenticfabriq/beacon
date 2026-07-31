"""Retry decorators for worker tick loops."""

from __future__ import annotations

from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast

from sqlalchemy.exc import OperationalError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

if TYPE_CHECKING:
    from collections.abc import Callable


P = ParamSpec("P")
R = TypeVar("R")


class WorkerError(Exception):
    """Base class for worker-control-flow errors."""


class TransientWorkerError(WorkerError):
    """Retryable worker error such as a network blip or DB connection drop."""


class FatalWorkerError(WorkerError):
    """Non-retryable worker error such as config drift or a programming bug."""


def retry_transient(
    *,
    max_attempts: int = 5,
    initial_wait_seconds: float = 1.0,
    max_wait_seconds: float = 30.0,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Retry transient worker errors with jittered exponential backoff."""
    decorator = retry(
        retry=retry_if_exception_type((TransientWorkerError, OperationalError)),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=initial_wait_seconds, max=max_wait_seconds),
        reraise=True,
    )
    return cast("Callable[[Callable[P, R]], Callable[P, R]]", decorator)
