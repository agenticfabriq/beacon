"""Live round-trip against the configured judge endpoint (skipped when unset)."""

from __future__ import annotations

import os

import pytest
from beacon_graders.llm.openai_provider import OpenAICompatibleProvider
from beacon_graders.llm.provider import JudgeRequest

_REQUIRED = ("BEACON_JUDGE_BASE_URL", "BEACON_JUDGE_API_KEY", "BEACON_JUDGE_MODEL")


@pytest.mark.integration
@pytest.mark.skipif(
    not all(os.environ.get(name) for name in _REQUIRED),
    reason="BEACON_JUDGE_* not configured",
)
def test_judge_round_trip() -> None:
    provider = OpenAICompatibleProvider(
        base_url=os.environ["BEACON_JUDGE_BASE_URL"],
        api_key=os.environ["BEACON_JUDGE_API_KEY"],
        model=os.environ["BEACON_JUDGE_MODEL"],
    )
    response = provider.generate(
        JudgeRequest(
            prompt="Reply with the single word 'ok' and nothing else.",
            grader_version="test-v1",
            max_tokens=2048,
        )
    )
    assert "ok" in response.text.lower()
