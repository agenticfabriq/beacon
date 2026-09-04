"""Let an attribution say what its delta was measured over.

``gradeable_results`` drops ERROR attempts from both arms of a LOO sweep,
which is right -- an ERROR is the harness or the endpoint failing, not the
layer, and counting it as a failure lets an outage read as a quality
regression. But the exclusion was recorded nowhere, and ``metrics.py``'s own
docstring says what that costs: "Report the number of excluded items alongside
the rate, or a mostly-broken run looks healthy."

Three separate reductions happen between the submitted items and the number a
reader sees, and none of them was visible:

  * ERROR attempts leave, so an item whose every attempt errored leaves the
    denominator entirely rather than scoring zero
  * ``restrict_to_k_attempts`` drops any task with fewer than k attempts
  * ``bootstrap_paired_ci`` and ``mcnemar_exact`` then compare on the
    INTERSECTION of task ids, so a task surviving in one arm and not the other
    leaves the comparison

The asymmetry is the sharper risk. A layer removed from a working config can
fail in ways the baseline never does, so the arms shrink unevenly, and the
delta is then a comparison between two different samples. Both arms therefore
get their own count.

The empty case is the one worth naming: ``bootstrap_paired_ci`` returns
``(0.0, 0.0, 0.0)`` for an empty intersection and ``mcnemar_exact`` returns
``1.0``, so a sweep in which nothing could be graded is stored as a delta of
zero with a zero-width confidence interval -- the most confident claim the
schema can express, for a measurement that never happened. ``n_compared = 0``
is what makes that distinguishable from a real tie.

All four columns are NULLABLE and nothing is backfilled. A row written before
these existed does not know its counts, and writing 0 would assert that
nothing was excluded -- a measurement nobody took. Same reasoning as
``0020_unrecorded_token_cost``: a stored zero from a writer with nothing to
write is worse than an absent value, because it reads as data.

Only the HEADLINE k is promoted to columns, matching ``ci_low``, ``ci_high``
and ``mcnemar_p``. Every k's compared count is written into
``delta_pass_at_k`` and ``delta_pass_hat_k`` beside the delta it qualifies, so
a reader of any single rate has its sample size in the same object.

Storing it is half the job. The surfaces that publish a delta were emitting
the rate alone, which is the omission itself, so all four counts also go out
through ``GET /v1/suites/{id}/attribution`` and the ``layers_summary`` printed
by ``sweep.py``, ``run_fs_payments_ablation.py`` and
``run_mnemiq_bird_slice.py``.

Revision ID: 0021_ablation_exclusion_counts
Revises: 0020_unrecorded_token_cost
Create Date: 2026-09-04 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_ablation_exclusion_counts"
down_revision = "0020_unrecorded_token_cost"
branch_labels = None
depends_on = None

_COLUMNS = (
    "n_items_submitted",
    "n_baseline_excluded",
    "n_ablated_excluded",
    "n_compared",
)


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("attributions", sa.Column(name, sa.Integer(), nullable=True))

    # Each arm's tasks come from the submitted items and the compared set is
    # their intersection, so no count can exceed the submission. The two bounds
    # catch different mistakes: an INVERTED subtraction yields a negative,
    # which this one accepts (-8 <= 10) and the non-negative one refuses, while
    # this is the bound a future writer summing instead of subtracting crosses.
    op.create_check_constraint(
        "ck_attribution_sample_within_submission",
        "attributions",
        "n_items_submitted IS NULL OR ("
        "  (n_compared IS NULL OR n_compared <= n_items_submitted)"
        "  AND (n_baseline_excluded IS NULL OR n_baseline_excluded <= n_items_submitted)"
        "  AND (n_ablated_excluded IS NULL OR n_ablated_excluded <= n_items_submitted)"
        ")",
    )
    op.create_check_constraint(
        "ck_attribution_sample_non_negative",
        "attributions",
        "(n_items_submitted IS NULL OR n_items_submitted >= 0)"
        " AND (n_compared IS NULL OR n_compared >= 0)"
        " AND (n_baseline_excluded IS NULL OR n_baseline_excluded >= 0)"
        " AND (n_ablated_excluded IS NULL OR n_ablated_excluded >= 0)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_attribution_sample_non_negative", "attributions", type_="check")
    op.drop_constraint("ck_attribution_sample_within_submission", "attributions", type_="check")
    for name in reversed(_COLUMNS):
        op.drop_column("attributions", name)
