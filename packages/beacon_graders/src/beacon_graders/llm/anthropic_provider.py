"""Anthropic-backed LLM provider."""

from __future__ import annotations

import os
from typing import Any

import anthropic

from beacon_graders.errors import GraderJudgeError
from beacon_graders.llm.provider import JudgeRequest, JudgeResponse


class AnthropicLLMProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
            max_retries=max_retries,
        )

    @property
    def model_version(self) -> str:
        """Return the configured Anthropic model id used for judging."""
        return self._model

    def generate(self, request: JudgeRequest) -> JudgeResponse:
        """Call Anthropic Messages and return the concatenated text response."""
        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": request.max_tokens,
                "messages": [{"role": "user", "content": request.prompt}],
            }
            if request.system:
                kwargs["system"] = request.system
            response = self._client.messages.create(**kwargs)
        except anthropic.AnthropicError as exc:
            raise GraderJudgeError(f"Anthropic call failed: {exc!r}") from exc

        if not response.content:
            raise GraderJudgeError("Empty response from Anthropic")

        text = "".join(
            str(getattr(block, "text", ""))
            for block in response.content
            if getattr(block, "type", None) == "text"
        )
        return JudgeResponse(
            text=text,
            tokens_input=response.usage.input_tokens,
            tokens_output=response.usage.output_tokens,
            model_version=self._model,
            raw={"stop_reason": response.stop_reason, "id": response.id},
        )
