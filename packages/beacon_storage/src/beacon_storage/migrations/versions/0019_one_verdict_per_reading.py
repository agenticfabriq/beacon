"""One verdict per (result, metric, grader, grader_version).

A verdict is one grader version's reading of one result on one metric; a
second identical row is never a second opinion, only an accident (a re-import
followed by a regrade wrote the same reading twice, and count-based queries
over the table double-counted). The matrix survived it by aggregating with
bool_or; the next reader of the table would not.

Deletes exact-duplicate readings (keeping the earliest row -- ids are UUIDv7,
so lowest is oldest) and then makes recurrence impossible with a unique
index. ``metric`` is nullable in the schema; every writer sets it, and the
index treats NULLs as distinct, which cannot double-count anything.

Revision ID: 0019_one_verdict_per_reading
Revises: 0018_drop_project
Create Date: 2026-08-07 00:00:00
"""

from __future__ import annotations

from alembic import op

revision = "0019_one_verdict_per_reading"
down_revision = "0018_drop_project"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM verdicts extra
        USING verdicts keep
        WHERE extra.result_id = keep.result_id
          AND extra.metric IS NOT DISTINCT FROM keep.metric
          AND extra.grader = keep.grader
          AND extra.grader_version = keep.grader_version
          AND extra.id > keep.id
        """
    )
    op.create_index(
        "uq_verdicts_one_reading",
        "verdicts",
        ["result_id", "metric", "grader", "grader_version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_verdicts_one_reading", table_name="verdicts")
