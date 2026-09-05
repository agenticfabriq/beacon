"""Reconstructing a published number from what a regrade recorded."""

from __future__ import annotations

import pytest

from scripts.regrade_history import (
    _is_uuid,
    _per_run_delta,
    _result_counts_by_run,
    changes_from_this_event_onward,
    prior_pass_counts,
)


def test_a_flip_into_pass_is_counted_separately_from_a_flip_out() -> None:
    """A rate's numerator is a PASS count, so direction is the whole reading.

    "+2 PASS" and "-2 PASS" are different events with the same flip count, and
    a single `n_flipped` cannot tell them apart -- which is why the event
    records the before-value per result rather than a total.
    """
    delta = _per_run_delta(
        [
            {"run_id": "r1", "before": "FAIL", "after": "PASS"},
            {"run_id": "r1", "before": "FAIL", "after": "PASS"},
            {"run_id": "r1", "before": "PASS", "after": "FAIL"},
            {"run_id": "r2", "before": "PASS", "after": "FAIL"},
        ]
    )

    assert delta["r1"] == {"gained_pass": 2, "lost_pass": 1}
    assert delta["r2"] == {"gained_pass": 0, "lost_pass": 1}


@pytest.mark.parametrize(
    ("before", "after", "gained", "lost"),
    [
        ("FAIL", "PASS", 1, 0),
        ("PASS", "FAIL", 0, 1),
        ("DEFER", "PASS", 1, 0),
        ("PASS", "DEFER", 0, 1),
        # Neither side is PASS, so the PASS count does not move. The outcome
        # still changed and is still recorded; it just is not a numerator move.
        ("FAIL", "DEFER", 0, 0),
        ("ERROR", "FAIL", 0, 0),
    ],
)
def test_only_movement_across_the_PASS_boundary_changes_a_numerator(
    before: str, after: str, gained: int, lost: int
) -> None:
    delta = _per_run_delta([{"run_id": "r", "before": before, "after": after}])
    expected = {"gained_pass": gained, "lost_pass": lost}
    assert delta.get("r", {"gained_pass": 0, "lost_pass": 0}) == expected


def test_the_prior_count_is_recovered_by_reversing_the_recorded_move() -> None:
    """Calls the code that prints it, not a re-derivation of the same sum.

    The first version of this test recomputed `now - gained + lost` in its own
    body, so inverting the sign in the script left it green while every
    recovered number came out wrong in the one direction nobody would question.
    """
    changes = [
        {"run_id": "r1", "before": "FAIL", "after": "PASS"},
        {"run_id": "r1", "before": "FAIL", "after": "PASS"},
        {"run_id": "r1", "before": "PASS", "after": "FAIL"},
    ]

    # Two results entered PASS and one left, so the published number was 8.
    assert prior_pass_counts({"r1": 9}, [changes]) == {"r1": 8}


def test_recovery_reverses_EVERY_event_from_the_selected_one_onward() -> None:
    """Reversing one event against today's count is only right for the newest.

    With two flipping regrades on a suite -- the history this table exists to
    hold -- reversing just the selected event yields a number that was never
    published, and nothing on the row would mark it. So the older event's
    recovery has to unwind the newer one too.
    """
    older = [{"run_id": "r1", "before": "FAIL", "after": "PASS"}]  # +1
    newer = [{"run_id": "r1", "before": "FAIL", "after": "PASS"}] * 3  # +3

    # Today the run reads 9. Before the NEWER event it read 6; before the
    # OLDER one it read 5, not 8 -- which is what reversing only the older
    # event against today would have printed.
    assert prior_pass_counts({"r1": 9}, [newer]) == {"r1": 6}
    assert prior_pass_counts({"r1": 9}, [older, newer]) == {"r1": 5}


def test_recovery_is_order_independent_across_events() -> None:
    """Each event contributes its own net move, so the sum is additive."""
    a = [{"run_id": "r1", "before": "FAIL", "after": "PASS"}]
    b = [{"run_id": "r1", "before": "PASS", "after": "FAIL"}]

    assert prior_pass_counts({"r1": 4}, [a, b]) == prior_pass_counts({"r1": 4}, [b, a])


def test_the_recovery_defaults_a_missing_run_to_zero_rather_than_dropping_it() -> None:
    """A property of `prior_pass_counts` alone, not of the report.

    The report no longer reaches this default: `_result_counts_by_run` emits a
    row with `passes: 0` for an all-FAIL run, and a run missing from those
    counts is filtered as unknown before the recovery is read. What stays
    worth pinning is that the function itself accepts a partial map and
    reverses into it rather than skipping the run -- silently dropping a run
    would understate the recovery, and this is the arithmetic other callers
    would inherit.
    """
    changes = [{"run_id": "r_new", "before": "PASS", "after": "FAIL"}]

    assert prior_pass_counts({}, [changes]) == {"r_new": 1}


def test_a_change_with_no_run_id_is_still_counted() -> None:
    """A malformed entry must not silently vanish from the accounting.

    Dropping it would understate the recovery and make `n_flipped` disagree
    with the reconstruction, which is the disagreement the table's own check
    constraint exists to prevent.
    """
    delta = _per_run_delta([{"before": "FAIL", "after": "PASS"}])

    assert delta["?"] == {"gained_pass": 1, "lost_pass": 0}


def test_the_selection_takes_the_event_and_every_later_one() -> None:
    """The half of the recovery that chooses WHAT to reverse.

    Only the arithmetic half was pinned before. Narrowing the slice to the
    selected event alone left all twelve tests green while an older event
    reported the never-published number the multi-event fix exists to
    prevent -- and suppressed the note that would have flagged it.
    """
    a = [{"run_id": "r", "before": "FAIL", "after": "PASS"}]
    b = [{"run_id": "r", "before": "FAIL", "after": "PASS"}]
    c = [{"run_id": "r", "before": "PASS", "after": "FAIL"}]

    assert changes_from_this_event_onward([a, b, c], 0) == [a, b, c]
    assert changes_from_this_event_onward([a, b, c], 1) == [b, c]
    # The newest event reverses only itself, which is the one case where
    # reversing a single event against today's count was ever correct.
    assert changes_from_this_event_onward([a, b, c], 2) == [c]


@pytest.mark.parametrize("position", [-1, 3, 99])
def test_a_position_outside_the_event_list_is_refused(position: int) -> None:
    """Silently returning [] would recover "no change" for a real event."""
    with pytest.raises(IndexError):
        changes_from_this_event_onward([[], [], []], position)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("06a99a7c-0000-7000-8000-000000000000", True),
        ("?", False),
        ("", False),
        ("not-a-uuid", False),
    ],
)
def test_only_real_run_ids_reach_the_database(value: str, expected: bool) -> None:
    """`Result.run_id` is a uuid column, so the "?" bucket must not be queried.

    `_per_run_delta` buckets a change with no run id under "?" so it cannot
    vanish from the accounting. Feeding that into an `IN` list makes Postgres
    reject the whole query and the report dies instead of printing -- and the
    filter preventing it had no test, so deleting it stayed green.
    """
    assert _is_uuid(value) is expected


def test_the_query_is_never_built_with_the_unknown_run_bucket() -> None:
    """Pins the FILTER'S USE, not just the predicate behind it.

    `_is_uuid` had a test and the line calling it did not, so deleting the
    filter stayed green while a live query would have died: `Result.run_id` is
    a uuid column, and Postgres rejects the whole `IN` list over one "?".
    Captures the statement a stub session receives and reads its bind params,
    which is the only way to see what would actually have been sent.
    """

    class _Result:
        def all(self) -> list[object]:
            return []

    class _StubSession:
        def __init__(self) -> None:
            self.statement: object | None = None

        def execute(self, statement: object) -> _Result:
            self.statement = statement
            return _Result()

    session = _StubSession()
    real = "06a99a7c-0000-7000-8000-000000000000"
    _result_counts_by_run(session, [real, "?"])  # type: ignore[arg-type]

    assert session.statement is not None, "expected a query to be built"
    params = session.statement.compile().params  # type: ignore[attr-defined]
    queried = next(v for k, v in params.items() if isinstance(v, list))
    assert queried == [real], f"only real run ids may be queried, got {queried}"


def test_no_query_is_issued_when_nothing_is_queryable() -> None:
    """All-unknown ids must short-circuit rather than send an empty IN list."""

    class _StubSession:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, statement: object) -> object:
            self.calls += 1
            raise AssertionError("should not query")

    session = _StubSession()
    assert _result_counts_by_run(session, ["?", "nope"]) == {}  # type: ignore[arg-type]
    assert session.calls == 0


def test_an_all_fail_run_is_told_apart_from_a_run_with_no_results() -> None:
    """Both are absent from a PASS-only count, and only one means zero.

    A run whose results are all FAIL has a real 0 PASS today, so its recovery
    is real. A run whose results were cascaded away or reset to un-graded has
    no basis for a number at all -- `outcome_changes` is JSONB with no FK to
    runs, so the event outlives them. Reading absence as 0 invents a recovery
    for the second; reading it as unknown throws away the first.
    """

    class _Result:
        def __init__(self, rows: list[tuple[str, int, int]]) -> None:
            self._rows = rows

        def all(self) -> list[tuple[str, int, int]]:
            return self._rows

    class _StubSession:
        def __init__(self, rows: list[tuple[str, int, int]]) -> None:
            self._rows = rows

        def execute(self, statement: object) -> _Result:
            return _Result(self._rows)

    all_fail = "06a99a7c-0000-7000-8000-000000000000"
    gone = "06a99a7d-0000-7000-8000-000000000000"

    # The all-FAIL run has results and no passes; the vanished one has no row.
    counts = _result_counts_by_run(
        _StubSession([(all_fail, 12, 0)]),  # type: ignore[arg-type]
        [all_fail, gone],
    )

    assert counts[all_fail] == {"results": 12, "passes": 0}
    assert gone not in counts, (
        "a run with no result rows must be absent, so the caller reports it "
        "unknown rather than inventing zero"
    )
