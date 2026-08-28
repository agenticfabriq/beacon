"""Let a result say its token cost was never measured.

``tokens_input`` and ``tokens_output`` were NOT NULL DEFAULT 0, so a runner
that cannot report cost had no way to say so and every writer without a
measurement wrote 0. Both file importers do (neither report format carries
per-item tokens), the harness does on the path where the SUT raised before
running, and the matrix medians those zeros into a tokens column that reads as
a configuration costing nothing -- beside models whose cost is real.

The backfill reads a zero as the absence it always was. In this schema's
lifetime no result has ever recorded a positive ``tokens_input``: an LLM call
cannot consume zero prompt tokens, so a 0 there is a writer with nothing to
write, never a measurement. That makes both cases safe to convert:

  * both halves 0 -- nothing was measured, so both become NULL
  * input 0 with output positive -- the in-process SUT reports one number and
    it is the output half; the input half was never measured, so only it
    becomes NULL and the real output measurement is kept

A total is then NULL unless both halves are recorded, which is the point: an
output count with the prompt assumed free understates a SQL agent's cost by
more than it reports, and a wrong total is worse than an absent one.

``runtime_ms`` is deliberately left NOT NULL. Every report format carries
``ms`` and every runner path measures elapsed time, so it has no unmeasured
case to express.

Revision ID: 0020_unrecorded_token_cost
Revises: 0019_one_verdict_per_reading
Create Date: 2026-08-27 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_unrecorded_token_cost"
down_revision = "0019_one_verdict_per_reading"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("tokens_input", "tokens_output"):
        op.alter_column(
            "results",
            column,
            existing_type=sa.Integer(),
            nullable=True,
            server_default=None,
        )
    # Written by a caller that had no measurement, not observed as zero.
    op.execute(
        "UPDATE results SET tokens_input = NULL, tokens_output = NULL "
        "WHERE tokens_input = 0 AND tokens_output = 0"
    )
    op.execute("UPDATE results SET tokens_input = NULL WHERE tokens_input = 0")


def downgrade() -> None:
    # A NULL going back to 0 is the claim this migration exists to remove, but
    # it is the only value a NOT NULL integer column can take, and downgrading
    # is restoring the old schema's semantics.
    op.execute("UPDATE results SET tokens_input = 0 WHERE tokens_input IS NULL")
    op.execute("UPDATE results SET tokens_output = 0 WHERE tokens_output IS NULL")
    for column in ("tokens_input", "tokens_output"):
        op.alter_column(
            "results",
            column,
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        )
