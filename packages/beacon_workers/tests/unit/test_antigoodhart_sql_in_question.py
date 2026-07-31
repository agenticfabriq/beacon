"""SQL-fragments-in-question anti-Goodhart heuristic."""

from __future__ import annotations

from beacon_storage.models.antigoodhart import AntigoodhartSeverity
from beacon_workers.antigoodhart.heuristics.sql_in_question import (
    EvalItemView,
    ScanContext,
    scan,
)


def _ctx(ngram: int = 5) -> ScanContext:
    return ScanContext(sql_ngram_size=ngram, team_share_threshold=0.6)


def test_no_leak_clean_question() -> None:
    item = EvalItemView(
        id=None,
        team_id=None,
        suite_id=None,
        question="What were Q3 2025 revenue totals?",
        gold_output={"sql": "SELECT sum(revenue) FROM sales WHERE quarter='Q3-2025'"},
        evidence=None,
        metadata={},
    )

    assert scan(item, _ctx()) == []


def test_table_name_leaks_via_ngram() -> None:
    item = EvalItemView(
        id=None,
        team_id=None,
        suite_id=None,
        question="please run SELECT sum(revenue) FROM sales WHERE quarter='Q3-2025'",
        gold_output={"sql": "SELECT sum(revenue) FROM sales WHERE quarter='Q3-2025'"},
        evidence=None,
        metadata={},
    )

    findings = scan(item, _ctx(ngram=5))

    assert len(findings) == 1
    assert findings[0].severity == AntigoodhartSeverity.HIGH
    assert "sum" in findings[0].evidence["ngram"]


def test_keyword_only_ngram_not_flagged() -> None:
    item = EvalItemView(
        id=None,
        team_id=None,
        suite_id=None,
        question="Use SELECT FROM WHERE GROUP BY to answer the question",
        gold_output={"sql": "SELECT x FROM t WHERE y GROUP BY z"},
        evidence=None,
        metadata={},
    )

    assert scan(item, _ctx(ngram=3)) == []


def test_no_gold_sql_skips() -> None:
    item = EvalItemView(
        id=None,
        team_id=None,
        suite_id=None,
        question="Explain the trend",
        gold_output={"narrative": "Sales rose 12% QoQ"},
        evidence=None,
        metadata={},
    )

    assert scan(item, _ctx()) == []


def test_ngram_size_respected() -> None:
    item = EvalItemView(
        id=None,
        team_id=None,
        suite_id=None,
        question="show sum revenue FROM",
        gold_output={"sql": "SELECT sum(revenue) FROM sales WHERE q='Q3'"},
        evidence=None,
        metadata={},
    )

    assert scan(item, _ctx(ngram=5)) == []
