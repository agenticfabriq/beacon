"""What a regrade is allowed to restate.

A regrade recomputes verdicts at the current grader and re-derives outcomes
from them. That derivation has already rewritten 699 outcomes once, so what it
may and may not touch is a rule worth pinning rather than a line of script.
"""

from __future__ import annotations

import pytest

from scripts.regrade_suite import outcome_is_the_graders_to_restate

ANSWERABLE: dict[str, object] = {"question": "how many payments settled?"}
UNANSWERABLE: dict[str, object] = {"question": "average customer LTV?", "answerable": False}


@pytest.mark.parametrize("outcome", ["PASS", "FAIL"])
def test_a_graded_answer_is_re_derived(outcome: str) -> None:
    assert outcome_is_the_graders_to_restate(ANSWERABLE, outcome) is True


@pytest.mark.parametrize("outcome", ["DEFER", "ERROR", "TIMEOUT", "None"])
def test_the_runners_own_statement_stands(outcome: str) -> None:
    """Whether a query was produced at all is not the grader's to restate."""
    assert outcome_is_the_graders_to_restate(ANSWERABLE, outcome) is False


@pytest.mark.parametrize("outcome", ["PASS", "FAIL"])
def test_a_declared_unanswerable_item_is_never_re_derived(outcome: str) -> None:
    """The refusal contract owns this outcome; a result-set match does not.

    Re-deriving it would credit an over-answer whose SQL happened to match gold
    and fail a refusal that pushed an empty row set. Today the refusal items'
    gold is empty, so the grader is inapplicable and the bug cannot fire -- but
    that is luck, not a rule, and gold is a curator's field.
    """
    assert outcome_is_the_graders_to_restate(UNANSWERABLE, outcome) is False


def test_only_an_explicit_false_declares_a_refusal() -> None:
    """A missing flag is an ordinary item, not an unanswerable one."""
    assert outcome_is_the_graders_to_restate({"answerable": True}, "PASS") is True
    assert outcome_is_the_graders_to_restate({}, "PASS") is True
