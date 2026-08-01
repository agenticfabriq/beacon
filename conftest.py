from __future__ import annotations

import os
from urllib.parse import urlparse

# A database is safe to wipe only if its name says it is disposable. The
# previous list matched the bare substring "beacon" and the "_dev" suffix, which
# admitted the dev database this guard exists to protect: `make test` against
# postgresql://.../beacon passed the check and destroyed real run history.
# Suffixes only, and no "dev" -- a dev database is precisely what must survive.
_SAFE_DB_NAME_SUFFIXES = ("_test", "_ci")
_DEFAULT_TEST_DB = "beacon_test"


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
    if lowered.endswith(_SAFE_DB_NAME_SUFFIXES):
        return
    raise RuntimeError(
        "Refusing to run tests against DATABASE_URL with database name "
        f"{db_name!r}: every package's conftest drops and recreates the public "
        "schema, which destroys whatever is in that database. Use a database "
        f"whose name ends in {' or '.join(_SAFE_DB_NAME_SUFFIXES)} (e.g. "
        f"{_DEFAULT_TEST_DB}), or set BEACON_ALLOW_TEST_DB_WIPE=1 to override."
    )


def pytest_configure() -> None:
    # Defaults to the test database, never the dev one: the previous default
    # pointed the whole suite at postgresql://.../beacon and wiped it.
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://beacon:beacon_dev@localhost:"
        f"{os.environ.get('POSTGRES_PORT', '5432')}/{_DEFAULT_TEST_DB}",
    )
    _assert_safe_test_database_url(os.environ["DATABASE_URL"])
