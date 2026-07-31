"""structlog JSON setup for Beacon workers."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, cast

import structlog

if TYPE_CHECKING:
    from structlog.typing import EventDict, Processor


_WORKER_NAME = "unknown"


def _add_worker_name(
    _logger: object,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    event_dict.setdefault("worker_name", _WORKER_NAME)
    return event_dict


def configure_logging(*, worker_name: str, log_level: str = "INFO") -> None:
    """Configure stdlib logging and structlog for worker JSON output."""
    global _WORKER_NAME
    _WORKER_NAME = worker_name

    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _add_worker_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger() -> structlog.BoundLogger:
    """Return the process-level structlog logger."""
    return cast("structlog.BoundLogger", structlog.get_logger())
