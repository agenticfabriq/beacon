"""Well-formed eval-item judge for promotion candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from beacon_graders.llm.provider import JudgeRequest

from beacon_workers.backoff import TransientWorkerError

JUDGE_PROMPT_VERSION = "promotion_well_formed_v1"

_JUDGE_PROMPT_TEMPLATE = """\
You are a strict QA reviewer for a database-grounded data agent eval framework.

Decide whether the following production trace is a WELL-FORMED EVAL ITEM
candidate. Three criteria must hold for a high score:

1. The question is ANSWERABLE FROM THE DATABASE CONTENT.
   It is not a meta-question, UI question, or feedback prompt.

2. The output contains a GOLD-COMPARABLE ARTIFACT.
   This may be a SQL query, structured list, number, or dict that can be
   compared programmatically.

3. The question is WELL-SCOPED.
   It is concrete and single-intent, not vague or broad.

Return a JSON object exactly in this shape and no other text:
{{
  "score": <float in [0, 1]>,
  "reason": "<one sentence justification>",
  "suggested_suite": <string suite name, or null>
}}

Score conventions:
- >= 0.85: all three criteria clearly met
- 0.7-0.85: criteria met with minor ambiguity
- < 0.7: reject

QUESTION:
{question}

OUTPUT:
{output}
"""


def build_judge_prompt(*, question: str, output: dict[str, Any]) -> str:
    """Render the well-formed-judge LLM prompt for a question/output pair."""
    return _JUDGE_PROMPT_TEMPLATE.format(
        question=question,
        output=json.dumps(output, ensure_ascii=True, indent=2),
    )


@dataclass(frozen=True)
class JudgeResult:
    kept: bool
    score: float
    reason: str
    suggested_suite: str | None
    raw: dict[str, Any]


class WellFormedJudge:
    def __init__(self, *, provider: object, min_score: float) -> None:
        self.provider = provider
        self.min_score = min_score

    def evaluate(self, *, question: str, output: dict[str, Any]) -> JudgeResult:
        """Score a candidate via the LLM judge and decide whether to keep it."""
        prompt = build_judge_prompt(question=question, output=output)
        try:
            raw = self._call_provider(prompt)
        except Exception as exc:
            raise TransientWorkerError(f"judge LLM call failed: {exc!r}") from exc

        score = _score(raw)
        reason = str(raw.get("reason") or "")
        suggested = raw.get("suggested_suite")
        suggested_suite = str(suggested) if suggested else None
        return JudgeResult(
            kept=score >= self.min_score,
            score=score,
            reason=reason,
            suggested_suite=suggested_suite,
            raw=raw,
        )

    def _call_provider(self, prompt: str) -> dict[str, Any]:
        judge_json = getattr(self.provider, "judge_json", None)
        if callable(judge_json):
            raw = judge_json(prompt=prompt, prompt_version=JUDGE_PROMPT_VERSION)
            return raw if isinstance(raw, dict) else {}

        generate = getattr(self.provider, "generate", None)
        if callable(generate):
            response = generate(
                JudgeRequest(
                    prompt=prompt,
                    grader_version=JUDGE_PROMPT_VERSION,
                    max_tokens=512,
                )
            )
            return _parse_json_object(getattr(response, "text", ""))

        raise TypeError("provider must expose judge_json(...) or generate(JudgeRequest)")


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _score(raw: dict[str, Any]) -> float:
    try:
        return float(raw.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0
