"""Worker config reads env vars with sane defaults."""

from __future__ import annotations

import pytest
from beacon_workers.config import WorkerConfig


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@h/db")

    cfg = WorkerConfig()

    assert cfg.database_url == "postgresql+psycopg://u@h/db"
    assert cfg.log_level == "INFO"
    assert cfg.health_port == 0
    assert cfg.promotion_interval_seconds == 3600
    assert cfg.convergence_interval_seconds == 30
    assert cfg.antigoodhart_interval_seconds == 300
    assert cfg.retention_interval_seconds == 86400
    assert cfg.retention_days == 90


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@h/db")
    monkeypatch.setenv("BEACON_WORKER_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("BEACON_WORKER_PROMOTION_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("BEACON_WORKER_RETENTION_DAYS", "30")

    cfg = WorkerConfig()

    assert cfg.log_level == "DEBUG"
    assert cfg.promotion_interval_seconds == 60
    assert cfg.retention_days == 30


def test_health_port_default_per_worker() -> None:
    from beacon_workers.config import default_health_port

    assert default_health_port("promotion") == 9101
    assert default_health_port("convergence") == 9102
    assert default_health_port("antigoodhart") == 9103
    assert default_health_port("retention") == 9104
    with pytest.raises(ValueError, match="unknown worker_name"):
        default_health_port("unknown")
