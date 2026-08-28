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


def _object(value: object) -> dict[str, Any]:
    """Return ``value`` when it is a JSON object, an empty one otherwise.

    Every level of a judge response is server-supplied and any of them can come
    back the wrong type. Reading through them with ``.get`` turns that into an
    AttributeError.

    Two consumers read what this parses, and the guards around it raise for
    different reasons in each.

    For the LLM graders it buys diagnosis only: they catch bare ``Exception``,
    an empty ``text`` raises inside ``extract_json`` anyway, and both roads end
    at the same failed verdict. All that differs is whether the justification
    names the field the server got wrong.

    For a reference SUT built on this provider it changes the outcome of a
    WRONG-SHAPED body, and to the right one. That SUT hands ``text`` straight
    to ``_parse_output``, so an empty string becomes a COMPLETED result with
    empty SQL and grades FAIL, counted against the model. Raising makes it
    ERROR instead, which is this tracker's founding distinction: an endpoint
    that returned an unusable shape means the attempt never produced an answer,
    and an error leaves the denominator rather than counting against the model.

    A well-formed body reporting no text -- ``"content": null``, or ``""``, or
    the key absent -- is deliberately NOT that case and still grades FAIL. All
    this level reads is that the response was well formed and carried no text;
    whether the model answered with nothing or was cut off is recorded in
    ``finish_reason`` and acted on nowhere -- the open question ``generate``
    describes.

    Shape-versus-payload is the line drawn TODAY; see ``generate`` for the one
    that is still open.

    Used only where absent is a real reading -- ``usage``, because cost nobody
    reported is the case the nullable columns exist to express, and losing a
    good verdict over unreadable accounting would be perverse. Not used for
    ``choices`` or ``message``, which carry the payload and raise.
    """
    return value if isinstance(value, dict) else {}


def _usage_count(usage: Mapping[str, Any], key: str) -> int | None:
    """Return one usage counter, or None where the server reported no number.

    Numeric strings count: some servers send ``"prompt_tokens": "12"`` and the
    reading it stands for is a measurement either way. Anything that is not a
    finite, non-negative number is not a count -- and it has to fail to None
    here rather than out of ``generate``, for the reason ``_object`` gives.

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
        """Issue one chat completion and return the concatenated text response.

        A malformed shape on the PAYLOAD path -- ``choices``, ``message``,
        ``content`` -- raises. A malformed ``usage`` does not; see ``_object``
        for why cost is the one level that degrades. A well-formed body carrying
        no text returns ``""``, which downstream grades against the model.

        That last line is drawn today and is not the only one there could be.
        ``finish_reason`` reports why a reply ended, and two of its values --
        ``length`` and ``content_filter`` -- say the ending came from a ceiling
        or a refusal rather than from the model answering badly. Which ceiling
        the field does not say, and the three call for opposite responses: the
        budget this module sets (``max_completion_tokens``, or ``max_tokens``
        on the legacy retry), the model's context window, which no request
        field controls, or -- on reasoning models, where the budget also covers
        hidden reasoning tokens -- that budget spent before any content. Raising
        the budget is the fix for the first and third and makes the second fail
        outright, so the cause has to be identified, not assumed. Both are
        counted against the model today, and this tracker has opinions about
        that shape of mistake: a deferral is not a failure, an outage is not a
        wrong answer.

        Unexamined, not settled, and deliberately not prejudged here -- what
        each value should grade as depends on the endpoint and the model, which
        this repo does not know. Two things that are true from here: nothing
        reads ``finish_reason`` (``generate`` puts it in ``raw`` and no caller
        touches it), so the distinction is unrecoverable downstream as things
        stand; and it cannot be reconstructed from the text, because ``length``
        arrives both with content and without.
        """
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

        choices = body.get("choices")
        if not isinstance(choices, list):
            raise GraderJudgeError(
                f"Judge endpoint returned {type(choices).__name__} choices, expected a list"
            )
        if not choices:
            raise GraderJudgeError("Empty response from judge endpoint")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise GraderJudgeError(f"Judge endpoint returned a {type(choice).__name__} choice")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise GraderJudgeError(
                f"Judge endpoint returned a {type(message).__name__} message, expected an object"
            )
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            # The content-parts form some compatible servers return. `str()` on
            # it yields a Python repr that reaches extract_json and fails there
            # on the quoting -- true, but it reports malformed JSON instead of
            # the shape the server actually sent.
            raise GraderJudgeError(
                f"Judge endpoint returned {type(content).__name__} content, expected a string"
            )
        text = content or ""
        tokens_input, tokens_output = _usage_counts(_object(body.get("usage")))
        return JudgeResponse(
            text=text,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            model_version=self._model,
            raw={
                "id": body.get("id"),
                "finish_reason": choice.get("finish_reason"),
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
                    # stdlib json raises three different things here and none
                    # of them says "judge": a JSONDecodeError for a non-JSON
                    # 200, a bare ValueError for an integer literal over the
                    # interpreter's digit limit, and a RecursionError -- not a
                    # ValueError at all -- for a body nested deeply enough.
                    # No caller outside this module catches GraderJudgeError --
                    # they all catch bare Exception -- so what this mostly
                    # converts is the message they record. The exception is
                    # `generate`'s own legacy-max_tokens retry, which wraps the
                    # call to this function and does discriminate; it re-raises
                    # these only because an unparseable body's message never
                    # mentions max_completion_tokens.
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
