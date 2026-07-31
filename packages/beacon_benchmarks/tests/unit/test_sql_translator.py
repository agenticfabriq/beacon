"""SQL query translation from SQLite query text to PostgreSQL query text."""

from __future__ import annotations

import pytest
from beacon_benchmarks.base.errors import SqlTranslationError
from beacon_benchmarks.ingest.sql_translator import translate_sqlite_to_postgres


def test_translate_simple_select_quotes_identifiers() -> None:
    sql = "SELECT `CDSCode` FROM schools WHERE `County` = 'Alameda'"
    out = translate_sqlite_to_postgres(sql)
    assert '"CDSCode"' in out
    assert '"County"' in out
    assert "`" not in out


def test_translate_preserves_limit_and_order_by() -> None:
    sql = "SELECT a FROM t ORDER BY a DESC LIMIT 5"
    out = translate_sqlite_to_postgres(sql)
    assert "ORDER BY" in out
    assert "LIMIT 5" in out


def test_translate_iif_to_case() -> None:
    sql = "SELECT IIF(x > 0, 'pos', 'neg') FROM t"
    out = translate_sqlite_to_postgres(sql)
    assert "CASE" in out
    assert "WHEN" in out
    assert "THEN" in out
    assert "IIF" not in out.upper()


def test_translate_datetime_function_raises() -> None:
    sql = "SELECT DATETIME('now') FROM t"
    with pytest.raises(SqlTranslationError, match="DATETIME"):
        translate_sqlite_to_postgres(sql)


def test_translate_string_concat_preserved() -> None:
    sql = "SELECT first || ' ' || last FROM users"
    out = translate_sqlite_to_postgres(sql)
    assert "||" in out


def test_translate_real_division_handled() -> None:
    sql = "SELECT CAST(a AS REAL) / b FROM t"
    out = translate_sqlite_to_postgres(sql)
    assert "DOUBLE PRECISION" in out
