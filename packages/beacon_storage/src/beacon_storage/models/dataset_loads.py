"""Append-only receipts for successful benchmark ingests."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from sqlalchemy import TIMESTAMP, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, IdMixin


class DatasetLoad(Base, IdMixin):
    """Receipt row written after translating a benchmark source into Postgres."""

    __tablename__ = "dataset_loads"

    benchmark_name: Mapped[str] = mapped_column(String(80), nullable=False)
    suite: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    target_schema: Mapped[str] = mapped_column(String(120), nullable=False)
    row_counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_dataset_loads_benchmark_suite", "benchmark_name", "suite"),
        Index("ix_dataset_loads_sha", "source_sha256"),
    )
