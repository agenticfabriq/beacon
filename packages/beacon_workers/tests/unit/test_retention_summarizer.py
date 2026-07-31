"""TraceSummarizer extracts compact trace summaries."""

from __future__ import annotations

from beacon_workers.retention.summarizer import TraceSummarizer


def test_summarize_extracts_verdicts() -> None:
    payload = {
        "trace_tree": {"steps": [{"id": "s1", "kind": "tool"} for _ in range(100)]},
        "judge_responses": ["x" * 5000, "y" * 5000],
    }
    verdicts = [
        {
            "grader": "ExecutionGroundedSqlGrader",
            "outcome": "PASS",
            "score": 1.0,
            "answer_hash": "abc",
            "confidence": 0.95,
        },
        {
            "grader": "DabstepAnswerMatcher",
            "outcome": "FAIL",
            "score": 0.0,
            "answer_hash": "def",
            "confidence": 0.6,
        },
    ]
    metrics = {
        "latency_ms": 234,
        "cost_usd": 0.012,
        "tokens_in": 1500,
        "tokens_out": 800,
    }

    summary = TraceSummarizer().summarize(
        payload=payload,
        verdicts=verdicts,
        metrics=metrics,
    )

    assert summary["verdicts"] == verdicts
    assert summary["metrics"] == metrics
    assert summary["original_size_bytes"] > 0
    assert "summarized_at" in summary
    assert summary["summary_version"] == "v1"


def test_summarize_empty_payload() -> None:
    summary = TraceSummarizer().summarize(payload={}, verdicts=[], metrics={})

    assert summary["verdicts"] == []
    assert summary["original_size_bytes"] == 2


def test_summarize_preserves_pass_overall() -> None:
    summarizer = TraceSummarizer()

    sum_pass = summarizer.summarize(
        payload={},
        verdicts=[{"outcome": "PASS"}, {"outcome": "PASS"}],
        metrics={},
    )
    sum_mixed = summarizer.summarize(
        payload={},
        verdicts=[{"outcome": "PASS"}, {"outcome": "FAIL"}],
        metrics={},
    )
    sum_empty = summarizer.summarize(payload={}, verdicts=[], metrics={})

    assert sum_pass["pass"] is True
    assert sum_mixed["pass"] is False
    assert sum_empty["pass"] is False
