"""The fs payments suite: does certified semantic meaning change the answer?"""

from __future__ import annotations

from beacon_benchmarks.fs_payments.ingest_items import (
    DATASET_VERSION,
    HEADLINE_METRIC,
    SUITE,
    FsPaymentsTask,
    IngestResult,
    execute_gold,
    ingest_fs_payments_tasks,
    load_tasks,
)

__all__ = [
    "DATASET_VERSION",
    "HEADLINE_METRIC",
    "SUITE",
    "FsPaymentsTask",
    "IngestResult",
    "execute_gold",
    "ingest_fs_payments_tasks",
    "load_tasks",
]
