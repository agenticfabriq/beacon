"""OpenAI-compatible chat-completions provider.

Any endpoint speaking the OpenAI chat-completions dialect works: the provider
is configured entirely by ``base_url`` + ``api_key`` + ``model``, which come
from ``BEACON_JUDGE_*`` in the environment (never from code defaults -- the
endpoint is deployment configuration, not source).
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

import httpx

from beacon_graders.errors import GraderJudgeError
from beacon_graders.llm.provider import JudgeRequest, JudgeResponse

if TYPE_CHECKING:
    from collections.abc import Mapping

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _usage_count(usage: Mapping[str, Any], key: str) -> int | None:
    """Return one usage counter, or None where the server reported no number.

    Numeric strings count: some servers send ``"prompt_tokens": "12"`` and the
    reading it stands for is a measurement either way. Anything that is not a
    finite, non-negative number is not a count -- and it has to fail to None
    here rather than out of ``generate``, which callers discriminate on
    ``GraderJudgeError``.

    ``float()`` is the throwing step and it throws two ways: ``ValueError`` on
    a string that is not a number, ``OverflowError`` on an int past the ~309
    digits it can represent. Both are reachable, because httpx parses bodies
    with stdlib json, which accepts bare ``Infinity`` and ``NaN`` tokens and
    integers far longer than that. (A longer one still -- past
    ``sys.get_int_max_str_digits()``, 4300 by default -- never arrives here:
    ``_post_with_retries`` fails to parse it and raises GraderJudgeError.)
    """
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number)


def _usage_counts(usage: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Split a usage block into (prompt, completion), absences preserved.

    A server that omits ``usage`` has not told us the call was free, and the
    reference SUTs persist these values -- so the absence has to survive rather
    than coerce to 0.

    A zeroed block is the same absence wearing a number. An LLM call cannot
    consume zero prompt tokens, so a 0 there is a server filling in a field it
    did not measure -- the reading migration 0020 applies to stored zeros, and
    unlike those rows nothing would come along later to clean this one up. An
    omitted ``prompt_tokens`` is that same missing half, so both cases have to
    take the branch. A zero completion count survives only when the prompt half
    was actually counted; on its own an empty reply is a real outcome.

    This distrust is specific to a third-party server auto-filling a field it
    did not measure. It is NOT the rule at the ingest boundary, where a runner
    that sends 0 is a caller asserting a number and is believed -- see
    ``schemas/ingest.py``. Same value, different writer, different reading.
    """
    prompt = _usage_count(usage, "prompt_tokens")
    completion = _usage_count(usage, "completion_tokens")
    if prompt == 0:
        prompt = None
    if completion == 0 and prompt is None:
        completion = None
    return prompt, completion


class OpenAICompatibleProvider:
    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        if not base_url or not api_key or not model:
            raise GraderJudgeError("judge base_url, api_key and model are all required")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._max_retries = max_retries

    @property
    def model_version(self) -> str:
        """Return the configured model id used for judging."""
        return self._model

    def generate(self, request: JudgeRequest) -> JudgeResponse:
        """Issue one chat completion and return the concatenated text response."""
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        payload = {
            "model": self._model,
            "max_completion_tokens": request.max_tokens,
            "messages": messages,
        }

        try:
            body = self._post_with_retries(payload)
        except GraderJudgeError as exc:
            # Older OpenAI-compatible servers (vLLM and friends) predate
            # max_completion_tokens; retry once in the legacy spelling.
            if "max_completion_tokens" not in str(exc):
                raise
            payload.pop("max_completion_tokens")
            payload["max_tokens"] = request.max_tokens
            body = self._post_with_retries(payload)

        choices = body.get("choices") or []
        if not choices:
            raise GraderJudgeError("Empty response from judge endpoint")
        text = str((choices[0].get("message") or {}).get("content") or "")
        usage = body.get("usage") or {}
        tokens_input, tokens_output = _usage_counts(usage)
        return JudgeResponse(
            text=text,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            model_version=self._model,
            raw={
                "id": body.get("id"),
                "finish_reason": choices[0].get("finish_reason"),
            },
        )

    def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = httpx.post(url, json=payload, headers=headers, timeout=self._timeout_s)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code == 200:
                    # Nothing stdlib json raises here is something a caller
                    # discriminating on GraderJudgeError can catch: a
                    # JSONDecodeError for a non-JSON 200, a bare ValueError for
                    # an integer literal over the interpreter's digit limit, and
                    # a RecursionError -- not a ValueError at all -- for a body
                    # nested deeply enough.
                    try:
                        parsed = response.json()
                    except (ValueError, RecursionError) as exc:
                        raise GraderJudgeError(
                            f"Judge endpoint returned an unparseable body: {exc}"
                        ) from exc
                    if not isinstance(parsed, dict):
                        raise GraderJudgeError("Judge endpoint returned a non-object body")
                    return parsed
                if response.status_code not in _RETRYABLE_STATUS:
                    raise GraderJudgeError(
                        f"Judge call failed: HTTP {response.status_code}: {response.text[:200]}"
                    )
                last_error = GraderJudgeError(f"HTTP {response.status_code}")
            if attempt < self._max_retries:
                time.sleep(0.5 * (attempt + 1))
        raise GraderJudgeError(f"Judge call failed after retries: {last_error!r}")
