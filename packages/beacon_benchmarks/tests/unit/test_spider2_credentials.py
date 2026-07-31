"""The credentials multiplex routes each task to the correct connection type."""

import pytest
from beacon_benchmarks.spider2_lite.credentials import (
    DialectRoute,
    route_task_to_dialect,
    secret_refs_for_dialect,
)


def test_route_sqlite_local() -> None:
    route = route_task_to_dialect({"db_id": "Bike_Store", "db_type": "sqlite"})
    assert route == DialectRoute.SQLITE


def test_route_bigquery() -> None:
    route = route_task_to_dialect({"db_id": "google_analytics_sample", "db_type": "bigquery"})
    assert route == DialectRoute.BIGQUERY


def test_route_snowflake() -> None:
    route = route_task_to_dialect({"db_id": "TPCH_SF1", "db_type": "snowflake"})
    assert route == DialectRoute.SNOWFLAKE


def test_route_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown db_type"):
        route_task_to_dialect({"db_id": "x", "db_type": "mysql"})


def test_secret_refs_for_bigquery() -> None:
    refs = secret_refs_for_dialect(DialectRoute.BIGQUERY)
    assert "spider2.bigquery.service_account_json" in refs
    assert "spider2.bigquery.project_id" in refs


def test_secret_refs_for_snowflake() -> None:
    refs = secret_refs_for_dialect(DialectRoute.SNOWFLAKE)
    assert "spider2.snowflake.account" in refs
    assert "spider2.snowflake.user" in refs
    assert "spider2.snowflake.password" in refs


def test_secret_refs_for_sqlite_empty() -> None:
    """SQLite ingests to local Postgres; no remote credentials needed."""
    assert secret_refs_for_dialect(DialectRoute.SQLITE) == []
