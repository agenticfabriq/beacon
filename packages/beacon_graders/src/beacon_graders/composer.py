"""Verdict composer implementing Swiss Cheese precedence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_graders.types import Verdict, VerdictOutcome

if TYPE_CHECKING:
    from collections.abc import Iterable

    from beacon_runner.types import EvalItem, ExecutionResult

    from beacon_graders.grader import Grader

_DEFAULT_EXECUTION_GRADERS = frozenset(
    {
        "execution_grounded_sql",
        "dabstep_answer_matcher",
        "text2vis_data_grounded",
    }
)
_DEFAULT_LLM_GRADERS = frozenset(
    {
        "hierarchical_rubric",
        "free_text_reference",
    }
)


class VerdictComposer:
    def __init__(
        self,
        *,
        graders: Iterable[Grader],
        execution_grader_names: frozenset[str] = _DEFAULT_EXECUTION_GRADERS,
        llm_judge_grader_names: frozenset[str] = _DEFAULT_LLM_GRADERS,
        llm_pass_threshold: float = 0.8,
    ) -> None:
        self.graders = list(graders)
        self.execution_grader_names = execution_grader_names
        self.llm_judge_grader_names = llm_judge_grader_names
        self.threshold = llm_pass_threshold

    def compose(
        self,
        item: EvalItem,
        result: ExecutionResult,
    ) -> tuple[list[Verdict], VerdictOutcome]:
        """Run applicable graders and reduce their verdicts via Swiss Cheese precedence."""
        verdicts: list[Verdict] = []
        any_error = False
        any_timeout = False

        if result.error:
            if result.error.upper() == "TIMEOUT":
                any_timeout = True
            else:
                any_error = True

        for grader in self.graders:
            try:
                if not grader.applicable(item, result):
                    continue
                emitted = grader.grade(item, result)
                verdicts.extend(emitted)
                if any(self._is_timeout_verdict(verdict) for verdict in emitted):
                    any_timeout = True
            except Exception as exc:
                verdicts.append(
                    Verdict(
                        grader=grader.name,
                        grader_version=getattr(grader, "version", "unknown"),
                        criterion="error",
                        bool_value=None,
                        value=0.0,
                        justification=f"grader raised: {exc!r}",
                        raw_output={"exception_type": type(exc).__name__},
                    )
                )
                any_error = True

        if any_error:
            return verdicts, VerdictOutcome.ERROR
        if any_timeout:
            return verdicts, VerdictOutcome.TIMEOUT

        for verdict in verdicts:
            if verdict.grader in self.execution_grader_names and verdict.bool_value is not None:
                return (
                    verdicts,
                    VerdictOutcome.PASS if verdict.bool_value else VerdictOutcome.FAIL,
                )

        llm_values = [
            verdict.value
            for verdict in verdicts
            if verdict.grader in self.llm_judge_grader_names and verdict.value is not None
        ]
        if llm_values:
            composite = sum(llm_values) / len(llm_values)
            return (
                verdicts,
                VerdictOutcome.PASS if composite >= self.threshold else VerdictOutcome.FAIL,
            )

        return verdicts, VerdictOutcome.ERROR

    def _is_timeout_verdict(self, verdict: Verdict) -> bool:
        return verdict.criterion.lower() == "timeout"
