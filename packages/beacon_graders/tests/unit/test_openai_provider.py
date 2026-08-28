"""OpenAI-compatible judge provider: request shape, parsing, retries."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from beacon_graders.errors import GraderJudgeError
from beacon_graders.llm.openai_provider import OpenAICompatibleProvider
from beacon_graders.llm.provider import JudgeRequest


def _provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(base_url="https://judge.example/v1", api_key="k", model="m-1")


def _ok_body(text: str = "verdict") -> dict[str, Any]:
    return {
        "id": "resp-1",
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }


def test_generate_parses_a_chat_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, *, json: Any, headers: Any, timeout: Any) -> httpx.Response:
        seen["url"] = url
        seen["json"] = json
        seen["headers"] = headers
        return httpx.Response(200, json=_ok_body())

    monkeypatch.setattr(httpx, "post", fake_post)

    response = _provider().generate(
        JudgeRequest(prompt="judge this", grader_version="v1", system="be strict")
    )

    assert response.text == "verdict"
    assert response.tokens_input == 12
    assert response.tokens_output == 3
    assert response.model_version == "m-1"
    assert seen["url"] == "https://judge.example/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer k"
    assert seen["json"]["model"] == "m-1"
    assert seen["json"]["max_completion_tokens"] == 4096
    assert [m["role"] for m in seen["json"]["messages"]] == ["system", "user"]


def test_legacy_servers_get_the_max_tokens_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 400 naming max_completion_tokens retries once in the legacy dialect."""
    payloads: list[dict[str, Any]] = []

    def fake_post(url: str, *, json: Any, headers: Any, timeout: Any) -> httpx.Response:
        payloads.append(json)
        if "max_completion_tokens" in json:
            return httpx.Response(400, text="Unrecognized request argument: max_completion_tokens")
        return httpx.Response(200, json=_ok_body("ok"))

    monkeypatch.setattr(httpx, "post", fake_post)

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1"))

    assert response.text == "ok"
    assert "max_tokens" in payloads[-1]


def test_a_non_retryable_status_fails_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_post(url: str, **_: Any) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="bad key")

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(GraderJudgeError, match="HTTP 401"):
        _provider().generate(JudgeRequest(prompt="p", grader_version="v1"))
    assert len(calls) == 1


def test_a_retryable_status_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [httpx.Response(503, text="busy"), httpx.Response(200, json=_ok_body("ok"))]

    def fake_post(url: str, **_: Any) -> httpx.Response:
        return responses.pop(0)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1"))

    assert response.text == "ok"


def test_exhausted_retries_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, **_: Any) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    with pytest.raises(GraderJudgeError, match="after retries"):
        _provider().generate(JudgeRequest(prompt="p", grader_version="v1"))


def test_an_empty_choices_list_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda url, **_: httpx.Response(200, json={"choices": []}))

    with pytest.raises(GraderJudgeError, match="Empty response"):
        _provider().generate(JudgeRequest(prompt="p", grader_version="v1"))


def test_missing_configuration_is_refused() -> None:
    with pytest.raises(GraderJudgeError, match="required"):
        OpenAICompatibleProvider(base_url="", api_key="k", model="m")


def test_a_response_without_usage_reports_no_token_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`or 0` turned a missing usage block into a measurement of zero.

    Not every OpenAI-compatible server returns `usage` -- local runtimes
    routinely omit it. Cost is nullable now precisely so an absence stays an
    absence, and this is the last writer that was still coercing one to 0:
    the reference SUTs pass this straight into the result they persist, where
    a 0 is believed and medianed into the matrix beside real costs.
    """
    body = {"id": "r", "choices": [{"message": {"content": "v"}, "finish_reason": "stop"}]}
    monkeypatch.setattr(
        httpx, "post", lambda url, *, json, headers, timeout: httpx.Response(200, json=body)
    )

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))

    assert response.tokens_input is None
    assert response.tokens_output is None


def test_a_partial_usage_block_keeps_the_half_it_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "id": "r",
        "choices": [{"message": {"content": "v"}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 7},
    }
    monkeypatch.setattr(
        httpx, "post", lambda url, *, json, headers, timeout: httpx.Response(200, json=body)
    )

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))

    assert response.tokens_input is None
    assert response.tokens_output == 7


def test_a_zeroed_usage_block_is_a_server_filling_in_a_field_it_did_not_measure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An LLM call cannot consume zero prompt tokens.

    The same reading migration 0020 applies to stored zeros: a 0 in the prompt
    half is a writer that had nothing to write. A server that sends the block
    zeroed rather than omitting it must not land a believed 0 in the tokens
    column -- and unlike the pre-migration rows, nothing would clean it up.
    """
    body = {
        "id": "r",
        "choices": [{"message": {"content": "v"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }
    monkeypatch.setattr(
        httpx, "post", lambda url, *, json, headers, timeout: httpx.Response(200, json=body)
    )

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))

    assert response.tokens_input is None
    assert response.tokens_output is None


def test_an_empty_reply_against_a_measured_prompt_keeps_its_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero completion tokens is a real outcome when the prompt was counted."""
    body = {
        "id": "r",
        "choices": [{"message": {"content": "v"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 0},
    }
    monkeypatch.setattr(
        httpx, "post", lambda url, *, json, headers, timeout: httpx.Response(200, json=body)
    )

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))

    assert response.tokens_input == 12
    assert response.tokens_output == 0


def test_a_usage_counter_sent_as_a_string_is_still_a_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`int(... or 0)` parsed these; the isinstance guard must not lose them."""
    body = {
        "id": "r",
        "choices": [{"message": {"content": "v"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": "12", "completion_tokens": "3"},
    }
    monkeypatch.setattr(
        httpx, "post", lambda url, *, json, headers, timeout: httpx.Response(200, json=body)
    )

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))

    assert response.tokens_input == 12
    assert response.tokens_output == 3


def test_a_zero_completion_count_with_no_prompt_count_is_not_a_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting `prompt_tokens` is the prompt half missing, same as sending 0.

    The zero-is-absence rule keyed on `prompt == 0` and let an omitted prompt
    fall straight through, so `{"completion_tokens": 0}` -- the block these
    servers already send, carrying the one value the rule exists to distrust --
    came back as a measured zero output.
    """
    body = {
        "id": "r",
        "choices": [{"message": {"content": "v"}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 0},
    }
    monkeypatch.setattr(
        httpx, "post", lambda url, *, json, headers, timeout: httpx.Response(200, json=body)
    )

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))

    assert response.tokens_input is None
    assert response.tokens_output is None


@pytest.mark.parametrize(
    # float("inf") is absent on purpose: it cannot be JSON-encoded to build the
    # body. The unquoted-Infinity path has its own test below, over raw content.
    "value",
    ["Infinity", "inf", "1e400", "nan", "NaN", "twelve", 10**400, -5],
)
def test_a_usage_counter_that_is_not_a_finite_number_is_no_count_at_all(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """Parsing must fail to None, never out of `generate` as OverflowError.

    Nothing may escape `generate` untyped. `int(float("Infinity"))` raises
    OverflowError, which the ValueError-only guard let out -- and stdlib
    json, which httpx uses, parses a bare `Infinity` token into a float, so the
    numeric path reaches it too. `float()` raises the same on an int too large
    to convert, which the earlier `int(value)` branch had handled: widening the
    parse to strings is what put every int through `float()`.

    A negative count is not a count either. `JudgeResponse` carries no `ge=0`
    the way the ingest boundary does, and the value lands in the verdict
    details a grader writes.
    """
    body = {
        "id": "r",
        "choices": [{"message": {"content": "v"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": value, "completion_tokens": 3},
    }
    monkeypatch.setattr(
        httpx, "post", lambda url, *, json, headers, timeout: httpx.Response(200, json=body)
    )

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))

    assert response.tokens_input is None
    assert response.tokens_output == 3


def test_a_bare_infinity_token_in_the_body_does_not_escape_as_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stdlib json parses `Infinity` unquoted; it must not reach int()."""
    raw = (
        '{"id": "r", "choices": [{"message": {"content": "v"}, "finish_reason": "stop"}], '
        '"usage": {"prompt_tokens": Infinity, "completion_tokens": 3}}'
    )
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, *, json, headers, timeout: httpx.Response(
            200, content=raw, headers={"content-type": "application/json"}
        ),
    )

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))

    assert response.tokens_input is None


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("not json at all", id="not json"),
        pytest.param('{"a": ' + "1" * 4400 + "}", id="int past json digit limit"),
    ],
)
def test_an_unparseable_200_body_is_a_judge_error(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """A parse failure has to arrive as the error this module documents.

    `response.json()` raised whatever stdlib json raised -- JSONDecodeError for
    a non-JSON body, a bare ValueError for an integer literal over the digit
    limit -- straight past every caller's except clause. Guarding the counters
    was only half of it while the frame that produces the body was still open.
    (RecursionError is the third of these and has its own test below, because
    provoking it from a literal body is interpreter-dependent.)

    `match` is not decoration: with the int-string-digit limit disabled the
    4400-digit body parses fine, `generate` raises a different GraderJudgeError
    about an empty response, and a bare `pytest.raises` would go green while
    the guard under test never ran.
    """
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, *, json, headers, timeout: httpx.Response(
            200, content=body, headers={"content-type": "application/json"}
        ),
    )

    with pytest.raises(GraderJudgeError, match="unparseable body"):
        _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))


def test_a_parse_that_exhausts_the_stack_is_a_judge_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RecursionError is not a ValueError, so it needs naming separately.

    Injected rather than provoked with a deeply nested body: how much nesting
    it takes is interpreter-dependent -- 3.14 checks real stack headroom rather
    than counting to a fixed limit, and needs an order of magnitude more before
    it raises at all. A literal body would pass here and fail on another Python
    this project supports, testing the interpreter rather than the guard.
    """
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, *, json, headers, timeout: httpx.Response(200, json={"ok": True}),
    )

    def _blow_the_stack(self: httpx.Response) -> Any:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(httpx.Response, "json", _blow_the_stack)

    with pytest.raises(GraderJudgeError, match="unparseable body"):
        _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))


def test_a_parse_failure_still_arrives_as_a_valueerror() -> None:
    """The guard rests on a dependency guarantee, so name the guarantee.

    `except (ValueError, RecursionError)` needs exactly one thing to be true:
    a parse failure arrives as a ValueError or a RecursionError. It does today,
    but httpx is a floor here (`httpx>=0.27`), not a pin. Nothing more is
    required -- an httpx that wrapped the decoder's error in a ValueError
    subclass would still be caught and the guard would still be right, so
    tightening this to `json.JSONDecodeError` would report a break that had not
    happened.

    `test_an_unparseable_200_body_is_a_judge_error` already exercises the real
    `Response.json()` -- it monkeypatches `httpx.post` and hands back a genuine
    response -- so an httpx that started raising a non-ValueError on a bad
    parse would fail it too. What this adds is localization: that one surfaces
    as an exception leaking out of `generate`, from anywhere inside it, while
    this points at the library call.

    Nothing here watches the real library for the RecursionError half:
    `test_a_parse_that_exhausts_the_stack_is_a_judge_error` injects it, because
    provoking it needs a literal body deep enough to be interpreter-dependent.
    """
    with pytest.raises(ValueError):
        httpx.Response(200, content="not json at all").json()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param({"choices": [None]}, "NoneType choice", id="null choice"),
        pytest.param({"choices": "abc"}, "str choices", id="choices not a list"),
        pytest.param({"choices": [3]}, "int choice", id="scalar choice"),
        pytest.param({"choices": [{"message": "hi"}]}, "str message", id="message not an object"),
        pytest.param({"choices": [{"message": None}]}, "NoneType message", id="null message"),
        pytest.param(
            {"choices": [{"finish_reason": "stop"}]}, "NoneType message", id="no message key"
        ),
        pytest.param(
            {"choices": [{"message": {"content": [{"type": "text", "text": "v"}]}}]},
            "list content",
            id="content as parts",
        ),
    ],
)
def test_a_body_of_the_wrong_shape_is_a_judge_error(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any], expected: str
) -> None:
    """Parsing as JSON is not the same as being the response we expect.

    Every level of a judge response is server-supplied and any of them can be
    the wrong type. Reading through them with `.get` turned that into an
    AttributeError -- the same hole the parse frame had, one level in.

    These raise rather than degrade because they carry the payload. For the LLM
    graders that only sharpens the message -- an empty text raises inside
    `extract_json` regardless. For a reference SUT built on this provider it
    changes FAIL into ERROR: a degraded empty string becomes a COMPLETED result
    with empty SQL, counted against the model, where an endpoint returning an
    unusable shape means the attempt never answered. That includes an absent
    message, which the first version of this guard let through while its own
    docstring said it must not.

    Each case asserts its message. Without that they are indistinguishable
    from each other, which is how `{"choices": "abc"}` came to be reported as
    an empty response.
    """
    monkeypatch.setattr(
        httpx, "post", lambda url, *, json, headers, timeout: httpx.Response(200, json=body)
    )

    with pytest.raises(GraderJudgeError, match=expected):
        _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))


def test_a_malformed_usage_block_is_unrecorded_cost_not_a_failed_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cost is the one level where degrading is the right reading.

    The judge answered; only its accounting is unreadable. Absent cost is
    exactly what the nullable columns exist to express, so this reports the
    verdict with no token count rather than discarding a usable response.
    """
    body = {"choices": [{"message": {"content": "v"}, "finish_reason": "stop"}], "usage": [1, 2]}
    monkeypatch.setattr(
        httpx, "post", lambda url, *, json, headers, timeout: httpx.Response(200, json=body)
    )

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))

    assert response.text == "v"
    assert response.tokens_input is None
    assert response.tokens_output is None


@pytest.mark.parametrize(
    "message",
    [
        pytest.param({"content": None}, id="null content"),
        pytest.param({"content": ""}, id="empty content"),
        pytest.param({"role": "assistant"}, id="no content key"),
    ],
)
def test_a_well_formed_body_with_no_text_is_an_answer_not_a_malformed_response(
    monkeypatch: pytest.MonkeyPatch, message: dict[str, Any]
) -> None:
    """The line the shape guards draw is shape, not whether text arrived.

    `"content": null` is a real OpenAI shape and the server saying the model
    produced nothing. That is the model's answer and belongs against it -- the
    reference SUT grading it FAIL on empty SQL is correct, and turning it into
    a judge-endpoint error would move a real failure out of the denominator.
    """
    body = {"choices": [{"message": message, "finish_reason": "stop"}]}
    monkeypatch.setattr(
        httpx, "post", lambda url, *, json, headers, timeout: httpx.Response(200, json=body)
    )

    response = _provider().generate(JudgeRequest(prompt="p", grader_version="v1", system="s"))

    assert response.text == ""
