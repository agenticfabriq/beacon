"""Worker process configuration."""

from __future__ import annotations

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerConfig(BaseSettings):
    """Environment-driven settings shared by all worker processes."""

    database_url: str = Field(
        default="", validation_alias=AliasChoices("DATABASE_URL", "BEACON_WORKER_DATABASE_URL")
    )

    log_level: str = "INFO"
    health_port: int = 0
    object_storage: str = Field(
        default="local:///tmp/beacon-objects",
        validation_alias=AliasChoices("BEACON_OBJECT_STORAGE", "BEACON_WORKER_OBJECT_STORAGE"),
    )

    retention_interval_seconds: int = 86400
    retention_days: int = 90

    model_config = SettingsConfigDict(env_prefix="BEACON_WORKER_", case_sensitive=False)

    @field_validator("database_url")
    @classmethod
    def _database_url_required(cls, value: str) -> str:
        if not value:
            raise ValueError("database_url is required")
        return value


_DEFAULT_HEALTH_PORTS = {
    "retention": 9104,
}


def default_health_port(worker_name: str) -> int:
    """Return the conventional health port for a worker name."""
    if worker_name not in _DEFAULT_HEALTH_PORTS:
        raise ValueError(f"unknown worker_name: {worker_name}")
    return _DEFAULT_HEALTH_PORTS[worker_name]


def resolve_health_port(cfg: WorkerConfig, worker_name: str) -> int:
    """Return an explicit health port or the worker-specific default."""
    return cfg.health_port if cfg.health_port > 0 else default_health_port(worker_name)
