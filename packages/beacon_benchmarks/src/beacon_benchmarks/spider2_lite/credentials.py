"""Per-dialect secret-ref multiplex for Spider 2.0 Lite."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class DialectRoute(StrEnum):
    BIGQUERY = "bigquery"
    SNOWFLAKE = "snowflake"
    SQLITE = "sqlite"


def route_task_to_dialect(task: dict[str, Any]) -> DialectRoute:
    """Return the execution route for one Spider2 task manifest row."""
    raw = str(task.get("db_type", "")).lower()
    if raw in {"sqlite", "sqlite3"}:
        return DialectRoute.SQLITE
    if raw == "bigquery":
        return DialectRoute.BIGQUERY
    if raw == "snowflake":
        return DialectRoute.SNOWFLAKE
    raise ValueError(f"unknown db_type {raw!r} in task {task.get('instance_id')}")


def secret_refs_for_dialect(dialect: DialectRoute) -> list[str]:
    """Return secret refs required by a remote warehouse dialect."""
    if dialect == DialectRoute.BIGQUERY:
        return [
            "spider2.bigquery.service_account_json",
            "spider2.bigquery.project_id",
        ]
    if dialect == DialectRoute.SNOWFLAKE:
        return [
            "spider2.snowflake.account",
            "spider2.snowflake.user",
            "spider2.snowflake.password",
            "spider2.snowflake.warehouse",
        ]
    return []
