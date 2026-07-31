from __future__ import annotations

import os

import pytest
from beacon_graders.llm.anthropic_provider import AnthropicLLMProvider
from beacon_graders.llm.provider import JudgeRequest


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="No ANTHROPIC_API_KEY set",
)
def test_anthropic_provider_round_trip() -> None:
    provider = AnthropicLLMProvider(model="claude-haiku-4-5")
    response = provider.generate(
        JudgeRequest(
            prompt="Reply with the single word 'ok' and nothing else.",
            grader_version="test-v1",
            max_tokens=10,
        )
    )
    assert "ok" in response.text.lower()
    assert response.tokens_input > 0
