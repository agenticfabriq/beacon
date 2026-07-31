"""Promotion worker process entry point."""

from __future__ import annotations

import hashlib
import signal
import threading
from typing import TYPE_CHECKING

import numpy as np
from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-untyped]
from beacon_graders.llm import AnthropicLLMProvider
from beacon_storage.db import make_engine, make_session_factory

from beacon_workers.config import WorkerConfig, resolve_health_port
from beacon_workers.health import HealthState, start_health_server
from beacon_workers.logging import configure_logging, get_logger
from beacon_workers.promotion.judge import WellFormedJudge
from beacon_workers.promotion.worker import PromotionWorker

if TYPE_CHECKING:
    from collections.abc import Sequence


_WORKER_NAME = "promotion"


class _HashEmbeddingProvider:
    name = "hash"
    dim = 32

    def embed(self, *, texts: list[str]) -> list[np.ndarray]:
        """Return deterministic hash-derived embeddings, one per input text."""
        return [_hash_embedding(text, dim=self.dim) for text in texts]


def main(_argv: Sequence[str] | None = None) -> int:
    """Run the promotion-candidate extractor worker until stopped by a signal."""
    cfg = WorkerConfig()
    configure_logging(worker_name=_WORKER_NAME, log_level=cfg.log_level)
    log = get_logger()

    engine = make_engine(cfg.database_url)
    worker = PromotionWorker(
        factory=make_session_factory(engine),
        embedder=_build_embedder(cfg.embedding_provider),
        judge=WellFormedJudge(
            provider=_build_judge_provider(cfg.llm_provider),
            min_score=cfg.promotion_judge_min_score,
        ),
        novelty_threshold=cfg.promotion_novelty_threshold,
        batch_size=cfg.promotion_batch_size,
        max_per_team_per_tick=cfg.promotion_max_per_team_per_tick,
    )
    health = HealthState(expected_interval_seconds=cfg.promotion_interval_seconds)
    server = start_health_server(
        port=resolve_health_port(cfg, _WORKER_NAME),
        state=health,
    )
    scheduler = BackgroundScheduler(daemon=True)
    stop = threading.Event()

    def _tick() -> None:
        try:
            worker.tick_all_teams()
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
        seconds=cfg.promotion_interval_seconds,
        id="promotion-tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "worker.started",
        interval_seconds=cfg.promotion_interval_seconds,
        health_port=resolve_health_port(cfg, _WORKER_NAME),
    )
    _tick()

    stop.wait()
    scheduler.shutdown(wait=False)
    server.shutdown()
    engine.dispose()
    return 0


def _build_embedder(name: str) -> _HashEmbeddingProvider:
    if name == "hash":
        return _HashEmbeddingProvider()
    raise ValueError(
        f"unsupported embedding provider {name!r}; "
        "set BEACON_WORKER_EMBEDDING_PROVIDER=hash until a real embedding provider is wired"
    )


def _build_judge_provider(name: str) -> AnthropicLLMProvider:
    if name == "anthropic":
        return AnthropicLLMProvider()
    raise ValueError(f"unsupported LLM provider {name!r}")


def _hash_embedding(text: str, *, dim: int) -> np.ndarray:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = np.frombuffer(digest, dtype=np.uint8).astype(np.float32)[:dim]
    centered = values - 127.5
    norm = np.linalg.norm(centered)
    if norm == 0:
        return centered
    return centered / norm


if __name__ == "__main__":
    raise SystemExit(main())
