"""SQLite query to PostgreSQL query translator."""

from __future__ import annotations

import re

import sqlglot

from beacon_benchmarks.base.errors import SqlTranslationError

_UNSUPPORTED_FNS = re.compile(r"\b(DATETIME|STRFTIME|JULIANDAY)\s*\(", re.IGNORECASE)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_REAL_CAST_RE = re.compile(r"CAST\((?P<expr>.*?)\s+AS\s+REAL\)", re.IGNORECASE | re.DOTALL)


def _rewrite_backticks(text: str) -> str:
    return _BACKTICK_RE.sub(r'"\1"', text)


def _rewrite_real_casts(text: str) -> str:
    return _REAL_CAST_RE.sub(r"CAST(\g<expr> AS DOUBLE PRECISION)", text)


def translate_sqlite_to_postgres(sql: str) -> str:
    """Translate one SQLite query to PostgreSQL SQL text."""
    if _UNSUPPORTED_FNS.search(sql):
        raise SqlTranslationError(
            "unsupported SQLite function in query (DATETIME/STRFTIME/JULIANDAY)"
        )

    text = _rewrite_real_casts(_rewrite_backticks(sql))
    try:
        tree = sqlglot.parse_one(text, read="sqlite")
    except Exception as exc:  # noqa: BLE001
        raise SqlTranslationError(f"sqlglot parse failed: {exc}") from exc
    return tree.sql(dialect="postgres")
