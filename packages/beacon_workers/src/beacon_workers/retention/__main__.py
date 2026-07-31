"""Retention worker process entry point."""

from __future__ import annotations

import signal
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-untyped]
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.object_storage.local import LocalFsStorage

from beacon_workers.config import WorkerConfig, resolve_health_port
from beacon_workers.health import HealthState, start_health_server
from beacon_workers.logging import configure_logging, get_logger
from beacon_workers.retention.worker import RetentionWorker

if TYPE_CHECKING:
    from collections.abc import Sequence


_WORKER_NAME = "retention"


def main(_argv: Sequence[str] | None = None) -> int:
    """Run the retention summarizer worker until stopped by a signal."""
    cfg = WorkerConfig()
    configure_logging(worker_name=_WORKER_NAME, log_level=cfg.log_level)
    log = get_logger()

    engine = make_engine(cfg.database_url)
    worker = RetentionWorker(
        factory=make_session_factory(engine),
        storage=_build_storage(cfg.object_storage),
        retention_days=cfg.retention_days,
        batch_size=500,
    )
    health = HealthState(expected_interval_seconds=cfg.retention_interval_seconds)
    server = start_health_server(
        port=resolve_health_port(cfg, _WORKER_NAME),
        state=health,
    )
    scheduler = BackgroundScheduler(daemon=True)
    stop = threading.Event()

    def _tick() -> None:
        try:
            worker.tick()
        except Exception as exc:
            log.error("worker.tick_failed", error=repr(exc))
            return
        health.mark_success()

    def _request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    scheduler.add_job(
        _tick,
        "interval",
        seconds=cfg.retention_interval_seconds,
        id="retention-tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "worker.started",
        interval_seconds=cfg.retention_interval_seconds,
        health_port=resolve_health_port(cfg, _WORKER_NAME),
    )
    _tick()

    stop.wait()
    scheduler.shutdown(wait=False)
    server.shutdown()
    engine.dispose()
    return 0


def _build_storage(uri: str) -> LocalFsStorage:
    prefix = "local://"
    if not uri.startswith(prefix):
        raise ValueError("retention worker currently supports only local:// object storage")
    return LocalFsStorage(root=Path(uri.removeprefix(prefix)))


if __name__ == "__main__":
    raise SystemExit(main())
