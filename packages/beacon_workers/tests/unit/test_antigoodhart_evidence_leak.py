"""Evidence-leak anti-Goodhart heuristic."""

from __future__ import annotations

from beacon_storage.models.antigoodhart import AntigoodhartSeverity
from beacon_workers.antigoodhart.heuristics.evidence_leak import scan
from beacon_workers.antigoodhart.heuristics.sql_in_question import EvalItemView, ScanContext


def _ctx() -> ScanContext:
    return ScanContext(sql_ngram_size=5, team_share_threshold=0.6)


def test_numeric_answer_leaks_in_evidence() -> None:
    item = EvalItemView(
        id=None,
        team_id=None,
        suite_id=None,
        question="What was Q3 revenue?",
        gold_output={"answer": 4123550},
        evidence={"schema_card": "Q3 totals: 4123550 across all regions"},
        metadata={},
    )

    findings = scan(item, _ctx())

    assert len(findings) == 1
    assert findings[0].severity == AntigoodhartSeverity.HIGH


def test_no_evidence_no_finding() -> None:
    item = EvalItemView(
        id=None,
        team_id=None,
        suite_id=None,
        question="x?",
        gold_output={"answer": 100},
        evidence=None,
        metadata={},
    )

    assert scan(item, _ctx()) == []


def test_list_answer_leaks_through_nested_evidence() -> None:
    item = EvalItemView(
        id=None,
        team_id=None,
        suite_id=None,
        question="which products?",
        gold_output={"values": ["A1", "B2", "C3"]},
        evidence={"context": {"retrieved": ["unrelated", "A1, B2, C3 are top sellers"]}},
        metadata={},
    )

    findings = scan(item, _ctx())

    assert len(findings) == 1


def test_numeric_tolerance_avoids_spurious() -> None:
    item = EvalItemView(
        id=None,
        team_id=None,
        suite_id=None,
        question="x?",
        gold_output={"answer": 100},
        evidence={"unrelated_count": "we processed 27 rows"},
        metadata={},
    )

    assert scan(item, _ctx()) == []
