from __future__ import annotations

from beacon_graders.llm.provider import (
    JudgeCache,
    JudgeRequest,
    JudgeResponse,
    LLMProvider,
)


class _RecordingProvider:
    name = "fake"

    def __init__(self, model_version: str = "fake-1") -> None:
        self.model_version = model_version
        self.calls = 0

    def generate(self, request: JudgeRequest) -> JudgeResponse:
        self.calls += 1
        return JudgeResponse(
            text=f"reply-{request.prompt[:8]}",
            tokens_input=10,
            tokens_output=20,
            model_version=self.model_version,
            raw={"x": 1},
        )


def test_provider_satisfies_protocol() -> None:
    assert isinstance(_RecordingProvider(), LLMProvider)


def test_cache_returns_same_response_for_same_key() -> None:
    provider = _RecordingProvider()
    cache = JudgeCache(provider=provider)
    request = JudgeRequest(prompt="hello world", grader_version="v1")
    first = cache.get_or_call(request)
    second = cache.get_or_call(request)
    assert first.text == second.text
    assert provider.calls == 1


def test_cache_key_differs_by_grader_version() -> None:
    provider = _RecordingProvider()
    cache = JudgeCache(provider=provider)
    cache.get_or_call(JudgeRequest(prompt="p", grader_version="v1"))
    cache.get_or_call(JudgeRequest(prompt="p", grader_version="v2"))
    assert provider.calls == 2


def test_cache_key_differs_by_model_version() -> None:
    first_provider = _RecordingProvider()
    second_provider = _RecordingProvider(model_version="fake-2")
    first_cache = JudgeCache(provider=first_provider)
    second_cache = JudgeCache(provider=second_provider)
    first_cache.get_or_call(JudgeRequest(prompt="p", grader_version="v1"))
    second_cache.get_or_call(JudgeRequest(prompt="p", grader_version="v1"))
    assert first_provider.calls == 1
    assert second_provider.calls == 1


def test_cache_respects_max_size() -> None:
    provider = _RecordingProvider()
    cache = JudgeCache(provider=provider, max_size=2)
    cache.get_or_call(JudgeRequest(prompt="a", grader_version="v1"))
    cache.get_or_call(JudgeRequest(prompt="b", grader_version="v1"))
    cache.get_or_call(JudgeRequest(prompt="c", grader_version="v1"))
    cache.get_or_call(JudgeRequest(prompt="a", grader_version="v1"))
    assert provider.calls == 4
