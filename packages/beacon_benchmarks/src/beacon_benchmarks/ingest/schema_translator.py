"""SQLite DDL to PostgreSQL DDL translator."""

from __future__ import annotations

import re

from sqlalchemy.dialects.postgresql.base import RESERVED_WORDS

_TYPE_MAP = {
    "INTEGER": "BIGINT",
    "INT": "BIGINT",
    "TEXT": "TEXT",
    "REAL": "DOUBLE PRECISION",
    "FLOAT": "DOUBLE PRECISION",
    "DOUBLE": "DOUBLE PRECISION",
    "NUMERIC": "NUMERIC",
    "DECIMAL": "NUMERIC",
    "DATETIME": "TIMESTAMP",
    "DATE": "DATE",
    "BOOLEAN": "BOOLEAN",
    "BOOL": "BOOLEAN",
    "BLOB": "BYTEA",
}

_BACKTICK_RE = re.compile(r"`([^`]+)`")
_SIMPLE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?P<table>[^\s(]+)\s*\((?P<body>.*)\)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_TYPE_RE = re.compile(r"^(?P<type>[A-Za-z]+(?:\s*\([^)]*\))?)(?P<rest>.*)$", re.DOTALL)
_AUTOINCREMENT_RE = re.compile(r"\s+AUTOINCREMENT\b", re.IGNORECASE)
_REFERENCES_RE = re.compile(r"\s+REFERENCES\s+.*$", re.IGNORECASE | re.DOTALL)
_ZERO_DATE_DEFAULT_RE = re.compile(
    r"\s+DEFAULT\s+(['\"])(?:0000-00-00|0000-00-00 00:00:00)\1",
    re.IGNORECASE,
)
_TABLE_CONSTRAINT_PREFIXES = ("CONSTRAINT ", "FOREIGN KEY", "PRIMARY KEY", "UNIQUE ", "CHECK ")


def translate_type(sqlite_type: str) -> str:
    """Translate one SQLite column type token to PostgreSQL."""
    source = sqlite_type.strip().upper()
    match = re.match(r"^([A-Z]+)(\s*\([^)]*\))?$", source)
    if not match:
        return source
    base, paren = match.group(1), match.group(2) or ""
    mapped = _TYPE_MAP.get(base, base)
    if paren and base in {"VARCHAR", "CHAR", "NUMERIC", "DECIMAL"}:
        return f"{mapped}{paren}"
    return mapped


def requote_identifier(ident: str) -> str:
    """Replace SQLite backtick identifier quoting with PostgreSQL double quotes."""
    stripped = ident.strip()
    if stripped.startswith("`") and stripped.endswith("`"):
        return f'"{stripped[1:-1]}"'
    return stripped


def _rewrite_backticks(text: str) -> str:
    return _BACKTICK_RE.sub(r'"\1"', text)


def _strip_line_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def quote_identifier(ident: str) -> str:
    """Return the PostgreSQL spelling for a SQLite identifier."""
    stripped = requote_identifier(ident).strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        stripped = stripped[1:-1]
    if _SIMPLE_IDENTIFIER_RE.match(stripped):
        normalized = stripped.lower()
        if normalized in RESERVED_WORDS:
            return f'"{normalized}"'
        return normalized
    escaped = stripped.replace('"', '""')
    return f'"{escaped}"'


def _split_columns(body: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for idx, char in enumerate(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[start:idx].strip())
            start = idx + 1
    tail = body[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _split_identifier_and_rest(definition: str) -> tuple[str, str]:
    stripped = definition.strip()
    if stripped.startswith('"'):
        end = stripped.find('"', 1)
        if end == -1:
            return stripped, ""
        return stripped[: end + 1], stripped[end + 1 :].strip()
    pieces = stripped.split(maxsplit=1)
    if len(pieces) == 1:
        return pieces[0], ""
    return pieces[0], pieces[1]


def _is_table_constraint(definition: str) -> bool:
    upper = definition.lstrip().upper()
    return upper.startswith(_TABLE_CONSTRAINT_PREFIXES)


def _translate_column_def(definition: str) -> str:
    name, rest = _split_identifier_and_rest(definition)
    type_match = _TYPE_RE.match(rest)
    if not type_match:
        return f"{quote_identifier(name)} {rest}".rstrip()
    translated_type = translate_type(type_match.group("type"))
    suffix = _REFERENCES_RE.sub("", _AUTOINCREMENT_RE.sub("", type_match.group("rest")))
    suffix = _ZERO_DATE_DEFAULT_RE.sub("", suffix).strip()
    if suffix:
        return f"{quote_identifier(name)} {translated_type} {suffix}"
    return f"{quote_identifier(name)} {translated_type}"


def translate_create_table(ddl: str) -> str:
    """Translate a single SQLite ``CREATE TABLE`` statement to PostgreSQL."""
    text = _strip_line_comments(_rewrite_backticks(ddl))
    match = _CREATE_TABLE_RE.match(text)
    if not match:
        return text
    table = quote_identifier(match.group("table"))
    translated_defs = []
    for definition in _split_columns(match.group("body")):
        if _is_table_constraint(definition):
            continue
        translated_defs.append(_translate_column_def(definition))
    return f"CREATE TABLE {table} ({', '.join(translated_defs)})"
