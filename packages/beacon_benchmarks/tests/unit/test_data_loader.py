"""Bulk-load CSV serialization tests."""

from __future__ import annotations

from beacon_benchmarks.ingest.data_loader import _csv_copy_value


def test_csv_copy_value_preserves_empty_string() -> None:
    assert _csv_copy_value("") == ""


def test_csv_copy_value_uses_null_sentinel_for_none() -> None:
    assert _csv_copy_value(None) == "\\N"
