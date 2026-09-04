"""What a regrade is allowed to restate.

A regrade recomputes verdicts at the current grader and re-derives outcomes
from them. That derivation has already rewritten 699 outcomes once, so what it
may and may not touch is a rule worth pinning rather than a line of script.
"""

from __future__ import annotations

from pathlib import Path

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


def test_the_dry_run_signal_leaves_by_the_rollback_path() -> None:
    """`--dry-run` writes nothing because it RAISES, and that is load-bearing.

    `session_scope` commits on normal return and rolls back on exception, so a
    dry run cannot simply `return` after printing its summary -- that would
    commit every verdict it appended and every outcome it flipped. It leaves by
    raising `_DryRun`, which main() catches outside the scope.

    Worth a test of its own because the regrade is the one operation here that
    is NOT reversible: verdicts are appended (older versions stay, history not
    garbage), but `Result.outcome` is overwritten in place with no record of
    the previous value, and the flip count is printed only after the flush. On
    a corpus a peer has published numbers from, "run it and read the summary"
    means finding out too late.
    """
    from beacon_storage.db import session_scope

    from scripts.regrade_suite import _DryRun

    committed: list[str] = []
    rolled_back: list[str] = []

    class _FakeSession:
        def commit(self) -> None:
            committed.append("commit")

        def rollback(self) -> None:
            rolled_back.append("rollback")

        def close(self) -> None:
            pass

    with pytest.raises(_DryRun), session_scope(lambda: _FakeSession()):  # type: ignore[arg-type,misc]
        raise _DryRun(3)

    assert rolled_back == ["rollback"]
    assert committed == []


def test_the_dry_run_signal_carries_the_flip_count() -> None:
    """The number is the point: it is what tells you whether to run it for real."""
    from scripts.regrade_suite import _DryRun

    signal = _DryRun(7)

    assert signal.flipped == 7
    assert "7 outcome(s) would flip" in str(signal)


def test_the_dry_run_branch_RAISES_rather_than_returning() -> None:
    """Structural, because the behavioural test could not see the call site.

    `test_the_dry_run_signal_leaves_by_the_rollback_path` pins that
    `session_scope` rolls back on an exception -- but replacing the script's
    `raise _DryRun(flipped)` with `return 0` left it, and every other test
    here, green. A `return` inside the scope commits: the summary prints, the
    operator reads "DRY RUN -- nothing written", and every appended verdict and
    flipped outcome is already durable. Silent, irreversible, and worded as the
    opposite of what happened.

    So the invariant is asserted where it lives: the `--dry-run` branch inside
    `main` must leave by raising.
    """
    import ast

    source = (Path(__file__).resolve().parents[1] / "scripts" / "regrade_suite.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    main = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    branches = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Attribute)
        and node.test.attr == "dry_run"
    ]
    assert len(branches) == 1, "expected exactly one --dry-run branch in main()"

    body = branches[0].body
    assert any(isinstance(node, ast.Raise) for node in ast.walk(ast.Module(body, []))), (
        "the --dry-run branch must RAISE to reach session_scope's rollback"
    )
    assert not any(isinstance(node, ast.Return) for node in ast.walk(ast.Module(body, []))), (
        "a return inside session_scope COMMITS, which is the opposite of a dry run"
    )
