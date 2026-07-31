"""DatasetLoad model and repository wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_storage.models.dataset_loads import DatasetLoad
from beacon_storage.repository.dataset_loads import DatasetLoadRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


pytestmark = pytest.mark.integration


def test_create_dataset_load_receipt(session: Session) -> None:
    receipt = DatasetLoad(
        benchmark_name="bird",
        suite="bird_minidev_v2",
        dataset_version="v2-2026-06-05",
        source_path="fixtures/bird.sqlite",
        target_schema="bird_v2",
        row_counts={"questions": 500, "databases": 12},
        source_sha256="a" * 64,
    )

    session.add(receipt)
    session.commit()

    assert receipt.id is not None
    assert receipt.created_at is not None
    assert receipt.row_counts["questions"] == 500


def test_dataset_load_repo_records_and_returns_latest(session: Session) -> None:
    repo = DatasetLoadRepo(session)
    repo.record(
        benchmark_name="bird",
        suite="bird_minidev_v2",
        dataset_version="v1",
        source_path="fixtures/old.sqlite",
        target_schema="bird_v1",
        row_counts={"questions": 400},
        source_sha256="b" * 64,
    )
    newer = repo.record(
        benchmark_name="bird",
        suite="bird_minidev_v2",
        dataset_version="v2",
        source_path="fixtures/new.sqlite",
        target_schema="bird_v2",
        row_counts={"questions": 500},
        source_sha256="c" * 64,
    )

    latest = repo.latest_for_suite(benchmark_name="bird", suite="bird_minidev_v2")

    assert latest is not None
    assert latest.id == newer.id
    assert latest.dataset_version == "v2"


def test_dataset_load_repo_latest_for_suite_returns_none_when_absent(session: Session) -> None:
    latest = DatasetLoadRepo(session).latest_for_suite(
        benchmark_name="bird",
        suite="missing",
    )

    assert latest is None
