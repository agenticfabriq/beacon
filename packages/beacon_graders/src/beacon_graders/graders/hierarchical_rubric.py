"""LLM-judge grader for hierarchical rubric trees."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from beacon_graders.graders.judge_score import (
    criterion_score,
    cut,
    quote_judge_value,
    unscored_reason,
)
from beacon_graders.llm.prompts import HIERARCHICAL_RUBRIC_PROMPT, extract_json
from beacon_graders.llm.provider import JudgeRequest
from beacon_graders.types import GraderKind, Verdict

if TYPE_CHECKING:
    from beacon_runner.types import EvalItem, ExecutionResult

    from beacon_graders.llm.provider import JudgeCache


class HierarchicalRubricGrader:
    name = "hierarchical_rubric"
    version = "v1"
    kind = GraderKind.LLM_JUDGE
    metric: str | None = None

    def __init__(self, *, judge_cache: JudgeCache) -> None:
        self.cache = judge_cache

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:  # noqa: ARG002
        """Apply when ``item.metadata.rubric`` declares at least one named criterion."""
        return bool(self._criteria(item))

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:
        """Ask the LLM judge to score each rubric criterion independently."""
        rubric = item.metadata.get("rubric")
        rubric_payload = rubric if isinstance(rubric, dict) else {}
        criteria = self._criteria(item)
        prompt = HIERARCHICAL_RUBRIC_PROMPT.format(
            question=item.query.get("question", "(no question)"),
            candidate=self._candidate_text(result),
            gold=json.dumps(item.ground_truth or {}, indent=2),
            rubric_json=json.dumps(rubric_payload, indent=2),
        )
        request = JudgeRequest(prompt=prompt, grader_version=self.version)

        try:
            response = self.cache.get_or_call(request)
            parsed = extract_json(response.text)
        except Exception as exc:
            return [
                self._fail(str(criterion["name"]), f"Judge call failed: {cut(repr(exc))}")
                for criterion in criteria
            ]

        raw_scores = parsed.get("criteria")
        criteria_scores = raw_scores if isinstance(raw_scores, dict) else {}
        # A `criteria` that is present but not an object makes every criterion
        # look absent. It is not: the judge answered, in a shape the prompt did
        # not ask for, and telling the operator all of them are "missing"
        # denies a reply sitting in front of them.
        container_note = (
            None
            if raw_scores is None or isinstance(raw_scores, dict)
            else f"Judge output's 'criteria' is not an object: {quote_judge_value(raw_scores)}"
        )
        out: list[Verdict] = []
        for criterion in criteria:
            name = str(criterion["name"])
            payload = criteria_scores.get(name)
            # `_fail` said "missing" and scored it 0.0, which `composer.compose`
            # averages into the SUT's composite exactly like a real score -- so
            # the message reported an absence the number denied. `value=None`
            # declines to score, and the composer makes the item ERROR rather
            # than averaging whichever criteria survived -- unless something
            # decided it earlier: an execution verdict, a deferral, or an item
            # declared unanswerable all short-circuit first.
            scored = criterion_score(payload)
            if scored is None:
                out.append(
                    Verdict(
                        grader=self.name,
                        grader_version=self.version,
                        criterion=name,
                        value=None,
                        justification=container_note or unscored_reason(payload),
                        # The judge did answer; only this criterion is absent
                        # from what it said, so the call's evidence stands --
                        # same as the free-text grader does for this case.
                        raw_output={
                            "model_version": response.model_version,
                            "tokens_input": response.tokens_input,
                            "tokens_output": response.tokens_output,
                        },
                    )
                )
                continue
            value, justification = scored
            out.append(
                Verdict(
                    grader=self.name,
                    grader_version=self.version,
                    criterion=name,
                    value=value,
                    justification=justification,
                    raw_output={
                        "model_version": response.model_version,
                        "tokens_input": response.tokens_input,
                        "tokens_output": response.tokens_output,
                    },
                )
            )
        return out

    def _criteria(self, item: EvalItem) -> list[dict[str, Any]]:
        rubric = item.metadata.get("rubric")
        if not isinstance(rubric, dict):
            return []
        criteria = rubric.get("criteria")
        if not isinstance(criteria, list):
            return []
        return [
            criterion
            for criterion in criteria
            if isinstance(criterion, dict) and criterion.get("name")
        ]

    def _candidate_text(self, result: ExecutionResult) -> str:
        if "narrative" in result.output:
            return str(result.output["narrative"])
        if "answer" in result.output:
            return str(result.output["answer"])
        return json.dumps(result.output, default=str)

    def _fail(self, criterion: str, justification: str) -> Verdict:
        return Verdict(
            grader=self.name,
            grader_version=self.version,
            criterion=criterion,
            value=0.0,
            justification=justification,
            raw_output=None,
        )
