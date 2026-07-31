"""Anti-Goodhart worker process entry point."""

from __future__ import annotations

import signal
import threading
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-untyped]
from beacon_storage.db import make_engine, make_session_factory

from beacon_workers.antigoodhart.worker import AntiGoodhartWorker
from beacon_workers.config import WorkerConfig, resolve_health_port
from beacon_workers.health import HealthState, start_health_server
from beacon_workers.logging import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence


_WORKER_NAME = "antigoodhart"


def main(_argv: Sequence[str] | None = None) -> int:
    """Run the anti-Goodhart audit worker until stopped by a signal."""
    cfg = WorkerConfig()
    configure_logging(worker_name=_WORKER_NAME, log_level=cfg.log_level)
    log = get_logger()

    engine = make_engine(cfg.database_url)
    worker = AntiGoodhartWorker(
        factory=make_session_factory(engine),
        sql_ngram_size=cfg.antigoodhart_sql_ngram_size,
        team_share_threshold=cfg.antigoodhart_skew_team_share_threshold,
        batch_size=500,
    )
    health = HealthState(expected_interval_seconds=cfg.antigoodhart_interval_seconds)
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
        seconds=cfg.antigoodhart_interval_seconds,
        id="antigoodhart-tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "worker.started",
        interval_seconds=cfg.antigoodhart_interval_seconds,
        health_port=resolve_health_port(cfg, _WORKER_NAME),
    )
    _tick()

    stop.wait()
    scheduler.shutdown(wait=False)
    server.shutdown()
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
