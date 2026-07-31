"""WellFormedJudge structured JSON output and threshold gating."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from beacon_graders.llm.provider import JudgeResponse
from beacon_workers.promotion.judge import JudgeResult, WellFormedJudge, build_judge_prompt

if TYPE_CHECKING:
    from beacon_graders.llm.provider import JudgeRequest


def test_build_prompt_includes_question_and_output() -> None:
    prompt = build_judge_prompt(
        question="what are the top 5 products by revenue in Q3 2025?",
        output={"sql": "SELECT name FROM products ORDER BY revenue DESC LIMIT 5"},
    )

    assert "top 5 products by revenue" in prompt
    assert "SELECT name FROM products" in prompt
    assert "answerable from the database" in prompt.lower()


def test_judge_accepts_well_formed() -> None:
    provider = MagicMock()
    provider.judge_json.return_value = {
        "score": 0.92,
        "reason": "clear DB question with executable SQL output",
        "suggested_suite": "ad_hoc_sql",
    }
    judge = WellFormedJudge(provider=provider, min_score=0.7)

    result = judge.evaluate(
        question="what are the top 5 products by revenue in Q3?",
        output={"sql": "SELECT ..."},
    )

    assert isinstance(result, JudgeResult)
    assert result.kept is True
    assert result.score == 0.92
    assert result.suggested_suite == "ad_hoc_sql"


def test_judge_rejects_below_threshold() -> None:
    provider = MagicMock()
    provider.judge_json.return_value = {
        "score": 0.4,
        "reason": "meta-question",
        "suggested_suite": None,
    }
    judge = WellFormedJudge(provider=provider, min_score=0.7)

    result = judge.evaluate(question="how does this product work?", output={})

    assert result.kept is False


def test_judge_handles_missing_fields() -> None:
    provider = MagicMock()
    provider.judge_json.return_value = {"reason": "bad output, no score"}
    judge = WellFormedJudge(provider=provider, min_score=0.7)

    result = judge.evaluate(question="x", output={})

    assert result.kept is False
    assert result.score == 0.0


def test_judge_handles_provider_exception() -> None:
    from beacon_workers.backoff import TransientWorkerError

    provider = MagicMock()
    provider.judge_json.side_effect = TimeoutError("LLM hang")
    judge = WellFormedJudge(provider=provider, min_score=0.7)

    with pytest.raises(TransientWorkerError):
        judge.evaluate(question="x", output={})


def test_judge_accepts_generate_provider() -> None:
    class _GenerateProvider:
        def __init__(self) -> None:
            self.request: JudgeRequest | None = None

        def generate(self, request: JudgeRequest) -> JudgeResponse:
            self.request = request
            return JudgeResponse(
                text='{"score": 0.8, "reason": "well scoped", "suggested_suite": null}',
                tokens_input=10,
                tokens_output=5,
                model_version="fake",
            )

    provider = _GenerateProvider()
    judge = WellFormedJudge(provider=provider, min_score=0.7)

    result = judge.evaluate(question="how many orders shipped yesterday?", output={"answer": 42})

    assert result.kept is True
    assert provider.request is not None
    assert provider.request.grader_version == "promotion_well_formed_v1"
