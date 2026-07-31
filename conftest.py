from __future__ import annotations

import os
import re
from urllib.parse import urlparse

_SAFE_DB_NAME_MARKERS = ("beacon", "_test", "_dev", "_ci", "test_", "dev_", "ci_")


def _extract_db_name(database_url: str) -> str:
    parsed = urlparse(database_url)
    path = parsed.path or ""
    return path.lstrip("/")


def _assert_safe_test_database_url(database_url: str) -> None:
    if os.environ.get("BEACON_ALLOW_TEST_DB_WIPE") == "1":
        return
    db_name = _extract_db_name(database_url)
    if not db_name:
        return
    lowered = db_name.lower()
    if any(marker in lowered for marker in _SAFE_DB_NAME_MARKERS):
        return
    if re.fullmatch(r"[a-z0-9_]+", lowered) and lowered.endswith(("_test", "_dev", "_ci")):
        return
    raise RuntimeError(
        "Refusing to run tests against DATABASE_URL with database name "
        f"{db_name!r}: package conftest fixtures drop and recreate the "
        "public schema. Use a database whose name contains 'beacon', "
        "'_test', '_dev', or '_ci', or set BEACON_ALLOW_TEST_DB_WIPE=1 "
        "to override."
    )


def pytest_configure() -> None:
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://beacon:beacon_dev@localhost:"
        f"{os.environ.get('POSTGRES_PORT', '5432')}/beacon",
    )
    _assert_safe_test_database_url(os.environ["DATABASE_URL"])
