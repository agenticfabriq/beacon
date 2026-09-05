from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_graders.composer import VerdictComposer
from beacon_graders.types import GraderKind, Verdict, VerdictOutcome

if TYPE_CHECKING:
    from collections.abc import Callable

    from beacon_runner.types import EvalItem, ExecutionResult


class _Fixed:
    """Test-double grader that declares applicability and returns canned verdicts.

    ``kind`` matters whenever a canned verdict carries this double's own ``name``:
    composition looks the emitting grader up by name and trusts what it declares.
    Where the verdict names a different grader, classification falls back to the
    legacy name sets and this value is not consulted.
    """

    version = "v1"

    def __init__(
        self,
        *,
        name: str,
        verdicts: list[Verdict] | None = None,
        applies: bool = True,
        raises: Exception | None = None,
        kind: GraderKind = GraderKind.EXECUTION,
    ) -> None:
        self.name = name
        self.kind = kind
        self._verdicts = verdicts or []
        self._applies = applies
        self._raises = raises

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:  # noqa: ARG002
        return self._applies

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:  # noqa: ARG002
        if self._raises is not None:
            raise self._raises
        return self._verdicts


def _v(
    grader: str,
    criterion: str,
    *,
    bool_value: bool | None = None,
    value: float | None = None,
) -> Verdict:
    return Verdict(
        grader=grader,
        grader_version="v1",
        criterion=criterion,
        bool_value=bool_value,
        value=value,
        justification="",
        raw_output=None,
    )


def test_pass_when_only_execution_grader_passes(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item()
    result = make_result(output={"answer": "x"}, output_kind="answer")
    composer = VerdictComposer(
        graders=[
            _Fixed(
                name="exec",
                verdicts=[_v("execution_grounded_sql", "correctness", bool_value=True, value=1.0)],
            )
        ]
    )
    verdicts, outcome = composer.compose(item, result)
    assert outcome == VerdictOutcome.PASS
    assert len(verdicts) == 1


def test_fail_when_only_execution_grader_fails(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item()
    result = make_result(output={"answer": "x"}, output_kind="answer")
    composer = VerdictComposer(
        graders=[
            _Fixed(
                name="exec",
                verdicts=[_v("execution_grounded_sql", "correctness", bool_value=False, value=0.0)],
            )
        ]
    )
    _, outcome = composer.compose(item, result)
    assert outcome == VerdictOutcome.FAIL


def test_error_when_execution_result_has_error(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item()
    result = make_result(output={}, output_kind="json", error="boom")
    composer = VerdictComposer(
        graders=[
            _Fixed(
                name="exec",
                verdicts=[_v("execution_grounded_sql", "correctness", bool_value=False, value=0.0)],
            )
        ]
    )
    _, outcome = composer.compose(item, result)
    assert outcome == VerdictOutcome.ERROR


def test_timeout_when_execution_result_marks_timeout(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item()
    result = make_result(output={}, output_kind="json", error="TIMEOUT")
    composer = VerdictComposer(graders=[])
    _, outcome = composer.compose(item, result)
    assert outcome == VerdictOutcome.TIMEOUT


def test_error_when_grader_raises(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item()
    result = make_result(output={"answer": "x"}, output_kind="answer")
    composer = VerdictComposer(graders=[_Fixed(name="boom", raises=RuntimeError("kaboom"))])
    verdicts, outcome = composer.compose(item, result)
    assert outcome == VerdictOutcome.ERROR
    assert any(verdict.criterion == "error" for verdict in verdicts)


def test_timeout_criterion_sets_timeout(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item()
    result = make_result(output={"answer": "x"}, output_kind="answer")
    composer = VerdictComposer(
        graders=[_Fixed(name="timeout", verdicts=[_v("timeout_grader", "timeout", value=0.0)])]
    )
    _, outcome = composer.compose(item, result)
    assert outcome == VerdictOutcome.TIMEOUT


def test_llm_fallback_when_no_execution_grader(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item()
    result = make_result(output={"answer": "x"}, output_kind="answer")
    composer = VerdictComposer(
        graders=[
            _Fixed(
                kind=GraderKind.LLM_JUDGE,
                name="hierarchical_rubric",
                verdicts=[
                    _v("hierarchical_rubric", "completeness", value=0.9),
                    _v("hierarchical_rubric", "clarity", value=0.85),
                ],
            )
        ]
    )
    _, outcome = composer.compose(item, result)
    assert outcome == VerdictOutcome.PASS


def test_llm_fallback_below_threshold(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item()
    result = make_result(output={"answer": "x"}, output_kind="answer")
    composer = VerdictComposer(
        graders=[
            _Fixed(
                kind=GraderKind.LLM_JUDGE,
                name="hierarchical_rubric",
                verdicts=[
                    _v("hierarchical_rubric", "completeness", value=0.6),
                    _v("hierarchical_rubric", "clarity", value=0.7),
                ],
            )
        ]
    )
    _, outcome = composer.compose(item, result)
    assert outcome == VerdictOutcome.FAIL


def test_execution_grader_wins_over_llm(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item()
    result = make_result(output={"answer": "x"}, output_kind="answer")
    composer = VerdictComposer(
        graders=[
            _Fixed(
                name="exec",
                verdicts=[_v("execution_grounded_sql", "correctness", bool_value=False, value=0.0)],
            ),
            _Fixed(
                kind=GraderKind.LLM_JUDGE,
                name="hierarchical_rubric",
                verdicts=[_v("hierarchical_rubric", "completeness", value=1.0)],
            ),
        ]
    )
    _, outcome = composer.compose(item, result)
    assert outcome == VerdictOutcome.FAIL


def test_skips_inapplicable_graders(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item()
    result = make_result(output={"answer": "x"}, output_kind="answer")
    composer = VerdictComposer(
        graders=[
            _Fixed(
                name="exec",
                applies=False,
                verdicts=[_v("execution_grounded_sql", "correctness", bool_value=False, value=0.0)],
            ),
            _Fixed(
                kind=GraderKind.LLM_JUDGE,
                name="hierarchical_rubric",
                verdicts=[_v("hierarchical_rubric", "completeness", value=0.95)],
            ),
        ]
    )
    verdicts, outcome = composer.compose(item, result)
    assert outcome == VerdictOutcome.PASS
    assert all(verdict.grader != "execution_grounded_sql" for verdict in verdicts)


def test_error_outcome_takes_precedence_over_llm_pass(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item()
    result = make_result(output={"answer": "x"}, output_kind="answer")
    composer = VerdictComposer(
        graders=[
            _Fixed(name="boom", raises=RuntimeError("x")),
            _Fixed(
                kind=GraderKind.LLM_JUDGE,
                name="hierarchical_rubric",
                verdicts=[_v("hierarchical_rubric", "completeness", value=1.0)],
            ),
        ]
    )
    _, outcome = composer.compose(item, result)
    assert outcome == VerdictOutcome.ERROR


def _exec_grader() -> _Fixed:
    """A grader whose verdict WOULD decide, if the pass/fail branch were reached."""
    return _Fixed(
        name="exec",
        verdicts=[_v("execution_grounded_sql", "correctness", bool_value=True, value=1.0)],
    )


def test_a_deferred_push_records_no_deciding_verdict(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    """The branch matters, and the verdict list cannot reveal which one it was.

    A deferral returns DEFER *before* the pass/fail branch, while the verdicts
    it returns still contain the execution verdict. So asking the verdict list
    "which decides" answers with a verdict that did NOT decide, and a caller
    recording that as the derivation writes a valid row claiming a grader
    decided an outcome the runner's deferral decided -- into a table the
    migration says cannot be backfilled or corrected.
    """
    composer = VerdictComposer(graders=[_exec_grader()])
    result = make_result(output={"answer": "x"}, output_kind="answer", deferred=True)

    verdicts, outcome, deciding = composer.compose_with_deciding(make_item(), result)

    assert outcome == VerdictOutcome.DEFER
    assert verdicts, "the execution verdict is still recorded as evidence"
    assert deciding is None, "the deferral decided, not the grader"


def test_an_errored_push_records_no_deciding_verdict(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    composer = VerdictComposer(graders=[_exec_grader()])
    result = make_result(output={"answer": "x"}, output_kind="answer", error="boom")

    _verdicts, outcome, deciding = composer.compose_with_deciding(make_item(), result)

    assert outcome == VerdictOutcome.ERROR
    assert deciding is None, "an instrument failure is not a grader's reading"


def test_the_refusal_contract_records_no_deciding_verdict(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    """Its PASS is indistinguishable from a grader's PASS by value alone.

    Which is exactly why an outcome value cannot be used to infer whether a
    grader decided -- this branch returns the same PASS/FAIL the grader branch
    does, so only the composer can tell them apart.
    """
    composer = VerdictComposer(graders=[_exec_grader()])
    item = make_item(query={"answerable": False})
    result = make_result(output={"rows": []}, output_kind="rows", deferred=True)

    _verdicts, outcome, deciding = composer.compose_with_deciding(item, result)

    assert outcome == VerdictOutcome.PASS
    assert deciding is None, "the runner's deferral decided this PASS"


def test_a_graded_pass_records_the_verdict_that_decided_it(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    composer = VerdictComposer(graders=[_exec_grader()])
    result = make_result(output={"answer": "x"}, output_kind="answer")

    _verdicts, outcome, deciding = composer.compose_with_deciding(make_item(), result)

    assert outcome == VerdictOutcome.PASS
    assert deciding is not None, "a grader decided; the derivation must name it"
    assert deciding.bool_value is True
