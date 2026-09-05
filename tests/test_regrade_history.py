"""Reconstructing a published number from what a regrade recorded."""

from __future__ import annotations

import pytest

from scripts.regrade_history import _per_run_delta, prior_pass_counts


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


def test_a_run_absent_from_the_current_counts_still_recovers() -> None:
    """A run whose results are all non-PASS today has no row in the counts.

    Treating a missing key as "no such run" would silently drop it from the
    recovery; it means zero PASS now, which is a number the reversal can use.
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
