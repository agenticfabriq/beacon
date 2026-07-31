from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from beacon_storage.models.dataset_loads import DatasetLoad

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class DatasetLoadRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        benchmark_name: str,
        suite: str,
        dataset_version: str,
        source_path: str,
        target_schema: str,
        row_counts: dict[str, Any],
        source_sha256: str,
    ) -> DatasetLoad:
        """Persist a dataset-load audit row capturing source and row counts."""
        dataset_load = DatasetLoad(
            benchmark_name=benchmark_name,
            suite=suite,
            dataset_version=dataset_version,
            source_path=source_path,
            target_schema=target_schema,
            row_counts=row_counts,
            source_sha256=source_sha256,
        )
        self.session.add(dataset_load)
        self.session.flush()
        return dataset_load

    def latest_for_suite(self, *, benchmark_name: str, suite: str) -> DatasetLoad | None:
        """Return the most recent dataset-load row for the benchmark/suite, or None."""
        return self.session.scalar(
            select(DatasetLoad)
            .where(
                DatasetLoad.benchmark_name == benchmark_name,
                DatasetLoad.suite == suite,
            )
            .order_by(DatasetLoad.created_at.desc(), DatasetLoad.id.desc())
            .limit(1)
        )
