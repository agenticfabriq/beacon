"""Heuristic: gold SQL n-grams leaking into an eval question."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from beacon_storage.models.antigoodhart import AntigoodhartKind, AntigoodhartSeverity

if TYPE_CHECKING:
    from uuid import UUID


_SQL_KEYWORDS = frozenset(
    {
        "all",
        "alter",
        "and",
        "as",
        "asc",
        "avg",
        "between",
        "by",
        "case",
        "count",
        "create",
        "delete",
        "desc",
        "distinct",
        "drop",
        "else",
        "end",
        "from",
        "group",
        "having",
        "in",
        "index",
        "inner",
        "insert",
        "into",
        "is",
        "join",
        "left",
        "like",
        "limit",
        "max",
        "min",
        "not",
        "null",
        "offset",
        "on",
        "or",
        "order",
        "outer",
        "right",
        "select",
        "set",
        "sum",
        "table",
        "then",
        "union",
        "update",
        "values",
        "view",
        "when",
        "where",
        "with",
    }
)
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class EvalItemView:
    id: UUID | None
    team_id: UUID | None
    suite_id: UUID | None
    question: str
    gold_output: dict[str, Any]
    evidence: dict[str, Any] | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ScanContext:
    sql_ngram_size: int
    team_share_threshold: float


@dataclass(frozen=True)
class FindingDraft:
    kind: AntigoodhartKind
    severity: AntigoodhartSeverity
    description: str
    evidence: dict[str, Any]
    item_id: UUID | None = None
    team_id: UUID | None = None
    suite_id: UUID | None = None


def scan(item: EvalItemView, ctx: ScanContext) -> list[FindingDraft]:
    """Flag gold-SQL token n-grams that reappear in the eval question."""
    sql = _extract_sql(item.gold_output)
    if sql is None or ctx.sql_ngram_size <= 0:
        return []

    sql_tokens = _tokenize(sql)
    question_tokens = _tokenize(item.question)
    ngram_size = ctx.sql_ngram_size
    if len(sql_tokens) < ngram_size or len(question_tokens) < ngram_size:
        return []

    question_ngrams = set(_ngrams(question_tokens, ngram_size))
    for ngram in _ngrams(sql_tokens, ngram_size):
        if _is_keyword_only(ngram) or ngram not in question_ngrams:
            continue

        has_identifier = any(_is_identifier(token) for token in ngram)
        return [
            FindingDraft(
                kind=AntigoodhartKind.SQL_IN_QUESTION,
                severity=(
                    AntigoodhartSeverity.HIGH if has_identifier else AntigoodhartSeverity.MEDIUM
                ),
                description=(
                    f"{ngram_size}-gram from gold SQL appears in question: {' '.join(ngram)}"
                ),
                evidence={
                    "ngram": " ".join(ngram),
                    "ngram_size": ngram_size,
                    "has_identifier_shape": has_identifier,
                },
                item_id=item.id,
                team_id=item.team_id,
                suite_id=item.suite_id,
            )
        ]

    return []


def _extract_sql(gold_output: dict[str, Any]) -> str | None:
    sql = gold_output.get("sql")
    if isinstance(sql, str) and sql.strip():
        return sql

    queries = gold_output.get("queries")
    if isinstance(queries, list):
        joined = " ; ".join(query for query in queries if isinstance(query, str))
        if joined.strip():
            return joined
    return None


def _tokenize(value: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(value)]


def _ngrams(tokens: list[str], size: int) -> list[tuple[str, ...]]:
    return [tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)]


def _is_keyword_only(tokens: tuple[str, ...]) -> bool:
    return all(token in _SQL_KEYWORDS for token in tokens)


def _is_identifier(token: str) -> bool:
    return token not in _SQL_KEYWORDS and _IDENT_RE.fullmatch(token) is not None
