"""Verdict composer implementing Swiss Cheese precedence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_graders.errors import BeaconGraderError
from beacon_graders.types import GraderKind, Verdict, VerdictOutcome

if TYPE_CHECKING:
    from collections.abc import Iterable

    from beacon_runner.types import EvalItem, ExecutionResult

    from beacon_graders.grader import Grader

_DEFAULT_EXECUTION_GRADERS = frozenset(
    {
        "execution_grounded_sql",
        "dabstep_answer_matcher",
        "result_set_match",
    }
)
_DEFAULT_LLM_GRADERS = frozenset(
    {
        "hierarchical_rubric",
        "free_text_reference",
    }
)


def _is_deferred(result: ExecutionResult) -> bool:
    """Return whether the solution declined to answer.

    Prefers the first-class ``deferred`` field; falls back to an
    ``output["deferred"]`` flag for solutions that report it that way.
    """
    if getattr(result, "deferred", False):
        return True
    return bool(result.output.get("deferred", False))


class AmbiguousPrimaryMetricError(BeaconGraderError):
    """Raised when the composer cannot tell which metric decides the outcome."""

    code = "ambiguous_primary_metric"


class VerdictComposer:
    def __init__(
        self,
        *,
        graders: Iterable[Grader],
        execution_grader_names: frozenset[str] = _DEFAULT_EXECUTION_GRADERS,
        llm_judge_grader_names: frozenset[str] = _DEFAULT_LLM_GRADERS,
        llm_pass_threshold: float = 0.8,
        primary_metric: str | None = None,
    ) -> None:
        self.graders = list(graders)
        self.primary_metric = self._resolve_primary_metric(primary_metric)
        # Fallback classification for graders that declare no ``kind``. Built-in
        # graders do declare one, and benchmark adapters rename their instances,
        # so these name sets must not be the primary signal -- see _kind_of.
        self.execution_grader_names = execution_grader_names
        self.llm_judge_grader_names = llm_judge_grader_names
        self.threshold = llm_pass_threshold

    @staticmethod
    def _stamp_metric(grader: object, emitted: list[Verdict]) -> list[Verdict]:
        """Label each verdict with the metric its grader declares.

        Stamped here rather than in every grader so a grader cannot emit a
        verdict whose metric disagrees with what it declared, and so existing
        graders need no change to participate.
        """
        metric = getattr(grader, "metric", None)
        if metric is None:
            return emitted
        return [
            v if v.metric is not None else v.model_copy(update={"metric": metric}) for v in emitted
        ]

    def _resolve_primary_metric(self, requested: str | None) -> str | None:
        """Decide which metric's verdict determines PASS/FAIL.

        Two execution graders are two readings of correctness -- a strict one
        and a tolerant one -- and both belong in the record. Only one can decide
        the outcome, and picking whichever the caller happened to list first
        made the same result compose PASS or FAIL by argument order. So an
        explicit choice is required as soon as the answer is not obvious.
        """
        # Every metric the graders EMIT, not only the one each declares as its
        # default. A grader that emits two readings can have either decide; a
        # name nothing emits still cannot, because that would put back the
        # argument-order dependence this check exists to remove -- nothing
        # would decide and the composer would fall through to ERROR.
        # The UNION of what each grader emits and what it declares as default,
        # never one replacing the other. An `elif` here read `emits` as the
        # whole set, so a grader declaring `emits = ("got_facts",)` as "the
        # additional readings" while keeping `metric = "exact_match"` had its
        # own stamped default silently dropped -- and the operator would then
        # see every push fail with `primary_metric 'exact_match' has no grader
        # declaring it` while every verdict in the database carried exactly
        # that name. A message pointing away from its cause.
        #
        # A name no grader emits is still refused: accepting one would restore
        # the argument-order dependence this check exists to remove, with
        # nothing matching as the deciding verdict and the composer falling
        # through to its terminal ERROR.
        declared: list[str] = []
        for grader in self.graders:
            emits = getattr(grader, "emits", None)
            if isinstance(emits, (tuple, list)):
                declared.extend(str(metric) for metric in emits)
            if (metric := getattr(grader, "metric", None)) is not None:
                declared.append(metric)
        if requested is not None:
            if requested not in declared:
                raise AmbiguousPrimaryMetricError(
                    f"primary_metric {requested!r} has no grader declaring it; "
                    f"declared metrics: {sorted(set(declared)) or '(none)'}"
                )
            return requested

        deciding = [
            grader for grader in self.graders if self._declared_kind(grader) is GraderKind.EXECUTION
        ]
        if len(deciding) > 1:
            names = sorted({getattr(g, "metric", None) or g.name for g in deciding})
            raise AmbiguousPrimaryMetricError(
                f"{len(deciding)} execution graders and no primary_metric: {names}. "
                "Pass primary_metric to say which one decides the outcome."
            )
        return None

    @staticmethod
    def _declared_kind(grader: object) -> GraderKind | None:
        kind = getattr(grader, "kind", None)
        return kind if isinstance(kind, GraderKind) else None

    def _decides_outcome(self, verdict: Verdict) -> bool:
        """Return whether this verdict is the one the outcome follows."""
        if self._kind_of(verdict.grader) is not GraderKind.EXECUTION:
            return False
        if self.primary_metric is None:
            return True
        return verdict.metric == self.primary_metric

    def _kind_of(self, grader_name: str) -> GraderKind | None:
        """Classify a verdict's emitting grader by what it declares, not its name.

        Adapters rewrite ``grader.name`` per suite (``bird_minidev_v2.exec_sql``,
        ``spider2_lite.exec_sql``, 13 sites), so matching the name against a
        frozen set silently composed ERROR for every real execution verdict.
        The instances the composer holds are the ones that were renamed, so
        their declared ``kind`` is looked up through the current name.
        """
        for grader in self.graders:
            if grader.name != grader_name:
                continue
            kind = getattr(grader, "kind", None)
            if isinstance(kind, GraderKind):
                return kind
            break
        if grader_name in self.execution_grader_names:
            return GraderKind.EXECUTION
        if grader_name in self.llm_judge_grader_names:
            return GraderKind.LLM_JUDGE
        return None

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
                emitted = self._stamp_metric(grader, grader.grade(item, result))
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

        # An item that DECLARES itself unanswerable judges the refusal, not a
        # result set: refusing IS the right answer, answering is the wrong one
        # regardless of what came back (decided 2026-08-08). Before this,
        # over-answering composed ERROR -- an instrument-failure label, in the
        # one band whose purpose is detecting over-answering -- and a correct
        # refusal composed DEFER, which the headline rate quietly penalizes.
        # Missing gold WITHOUT the declaration still falls through to ERROR
        # below: that absence is an ingest defect, and the label is correct.
        if item.query.get("answerable") is False:
            return verdicts, (VerdictOutcome.PASS if _is_deferred(result) else VerdictOutcome.FAIL)

        # Checked after error/timeout — an attempt that never ran cannot be
        # said to have declined — but before pass/fail, because there is no
        # answer to grade. Grader verdicts are still recorded as evidence.
        if _is_deferred(result):
            return verdicts, VerdictOutcome.DEFER

        for verdict in verdicts:
            if self._decides_outcome(verdict) and verdict.bool_value is not None:
                return (
                    verdicts,
                    VerdictOutcome.PASS if verdict.bool_value else VerdictOutcome.FAIL,
                )

        judged = [
            verdict for verdict in verdicts if self._kind_of(verdict.grader) is GraderKind.LLM_JUDGE
        ]
        # A judge verdict with no value declined to score that criterion -- the
        # reply never carried it, or carried no usable score. Averaging only the
        # criteria that survived hands the outcome to whichever ones did: 0.8
        # alone composes PASS where 0.8 beside a zeroed sibling composed FAIL.
        # A rubric half judged has not been judged, and ERROR is the label for
        # that -- the same one the all-unscored case already reaches by falling
        # through with nothing to average.
        if judged and any(verdict.value is None for verdict in judged):
            return verdicts, VerdictOutcome.ERROR
        llm_values = [verdict.value for verdict in judged if verdict.value is not None]
        if llm_values:
            composite = sum(llm_values) / len(llm_values)
            return (
                verdicts,
                VerdictOutcome.PASS if composite >= self.threshold else VerdictOutcome.FAIL,
            )

        return verdicts, VerdictOutcome.ERROR

    def _is_timeout_verdict(self, verdict: Verdict) -> bool:
        return verdict.criterion.lower() == "timeout"
