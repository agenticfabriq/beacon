"""LLM-judge grader for narrative outputs against reference insights."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from beacon_graders.graders.judge_score import criterion_score
from beacon_graders.llm.prompts import FREE_TEXT_REFERENCE_PROMPT, extract_json
from beacon_graders.llm.provider import JudgeRequest
from beacon_graders.types import GraderKind, Verdict

if TYPE_CHECKING:
    from beacon_runner.types import EvalItem, ExecutionResult

    from beacon_graders.llm.provider import JudgeCache

_CRITERIA = ("insight_recall", "citation_correctness")


class FreeTextReferenceGrader:
    name = "free_text_reference"
    version = "v1"
    kind = GraderKind.LLM_JUDGE
    metric: str | None = None

    def __init__(self, *, judge_cache: JudgeCache) -> None:
        self.cache = judge_cache

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:
        """Apply when ``result`` is a narrative and the item ships reference insights."""
        if result.output_kind != "narrative":
            return False
        reference_insights = (item.ground_truth or {}).get("reference_insights") or []
        return bool(reference_insights)

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:
        """Score insight recall and citation correctness via the LLM judge."""
        ground_truth = item.ground_truth or {}
        prompt = FREE_TEXT_REFERENCE_PROMPT.format(
            question=item.query.get("question", "(no question)"),
            candidate=str(result.output.get("narrative", "")),
            reference_insights=json.dumps(ground_truth.get("reference_insights", []), indent=2),
            citations=json.dumps(result.output.get("citations", []), indent=2),
            data_sources=json.dumps(ground_truth.get("data_sources", []), indent=2),
        )
        request = JudgeRequest(prompt=prompt, grader_version=self.version)

        try:
            response = self.cache.get_or_call(request)
            parsed = extract_json(response.text)
        except Exception as exc:
            return [self._fail(criterion, f"Judge call failed: {exc!r}") for criterion in _CRITERIA]

        evidence = {
            "model_version": response.model_version,
            "tokens_input": response.tokens_input,
            "tokens_output": response.tokens_output,
        }
        out: list[Verdict] = []
        for criterion in _CRITERIA:
            # A criterion the judge never scored is not a criterion scored 0.0.
            # `parsed.get(criterion) or {}` handed the default straight to
            # `payload.get("score", 0.0)`, so a judge that omitted one -- cut off
            # mid-JSON, or simply not emitting it -- produced a zero that
            # `composer.compose` averages into the SUT's composite exactly like a
            # real score. The composer skips `value is None`, which is how a
            # verdict says "nobody scored this"; a justification string alone
            # changes nothing, because no scoring path reads one.
            scored = criterion_score(parsed.get(criterion))
            if scored is None:
                out.append(
                    Verdict(
                        grader=self.name,
                        grader_version=self.version,
                        criterion=criterion,
                        value=None,
                        justification="Missing criterion in judge output",
                        # The judge did answer; only this criterion is absent
                        # from what it said, so the call's evidence stands.
                        raw_output=evidence,
                    )
                )
                continue
            value, justification = scored
            out.append(
                Verdict(
                    grader=self.name,
                    grader_version=self.version,
                    criterion=criterion,
                    value=value,
                    justification=justification,
                    raw_output=evidence,
                )
            )
        return out

    def _fail(self, criterion: str, justification: str) -> Verdict:
        return Verdict(
            grader=self.name,
            grader_version=self.version,
            criterion=criterion,
            value=0.0,
            justification=justification,
            raw_output=None,
        )
