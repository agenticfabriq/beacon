"""An unanswerable item judges the refusal, not a result set.

Found live by the fs payments refusal band: an item declaring
``answerable: false`` has no gold by construction, so no grader ever applied
and ANSWERING it composed ERROR -- over-answering wore an instrument-failure
label, in the one band whose purpose is detecting over-answering. Meanwhile a
correct refusal composed DEFER, which the headline rate quietly penalizes.

The contract (decided 2026-08-08): on a declared-unanswerable item, refusing
IS the right answer (PASS) and answering is the wrong one (FAIL) regardless
of what came back. An item whose gold is missing WITHOUT the declaration
stays ERROR -- that absence is an ingest defect, and the instrument label is
then correct. Errors and timeouts outrank everything: an outage on an
unanswerable item is still an outage.
"""

from __future__ import annotations

from beacon_graders.composer import VerdictComposer
from beacon_graders.types import VerdictOutcome
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep


def _item(*, answerable: bool | None) -> EvalItem:
    query: dict[str, object] = {"question": "q?"}
    if answerable is not None:
        query["answerable"] = answerable
    return EvalItem(
        item_id="i-1",
        suite="s",
        query=query,
        # Unanswerable by construction: nothing in the corpus answers it.
        ground_truth={"accepted_results": [], "sql": None},
        metadata={},
    )


def _result(*, deferred: bool, error: str | None = None) -> ExecutionResult:
    return ExecutionResult(
        output={"sql": "" if deferred else "SELECT 1", "deferred": deferred},
        output_kind="sql",
        trace=ExecutionStep(uuid="root", name="root", level="workflow"),
        tokens_input=1,
        tokens_output=1,
        runtime_ms=1,
        deferred=deferred,
        error=error,
    )


def test_a_correct_refusal_passes() -> None:
    _, outcome = VerdictComposer(graders=[]).compose(
        _item(answerable=False), _result(deferred=True)
    )

    assert outcome is VerdictOutcome.PASS


def test_answering_the_unanswerable_fails_rather_than_erroring() -> None:
    """Over-answering is a wrong answer whose wrongness is answering at all --
    not a crashed harness, which is what ERROR tells a reader."""
    _, outcome = VerdictComposer(graders=[]).compose(
        _item(answerable=False), _result(deferred=False)
    )

    assert outcome is VerdictOutcome.FAIL


def test_missing_gold_without_the_declaration_stays_an_ingest_defect() -> None:
    """Two different absences: 'no gold because unanswerable' is legitimate,
    'no gold because ingest failed' is the instrument's fault. Only the
    declared one changes the outcome."""
    _, outcome = VerdictComposer(graders=[]).compose(
        _item(answerable=None), _result(deferred=False)
    )

    assert outcome is VerdictOutcome.ERROR


def test_an_outage_on_an_unanswerable_item_is_still_an_outage() -> None:
    _, outcome = VerdictComposer(graders=[]).compose(
        _item(answerable=False), _result(deferred=False, error="engine exploded")
    )

    assert outcome is VerdictOutcome.ERROR
