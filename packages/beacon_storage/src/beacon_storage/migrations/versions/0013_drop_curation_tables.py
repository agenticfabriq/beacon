"""Drop the curation tables: production traces, provenance, eval-item suites' curation.

Curation moves to the semantic layer, which already carries a fuller version of
all of this: GoldenQuestion has a review workflow, versioning, deprecation and
per-question tolerance, and GoldenQuestionRecord.history is the audit trail
these provenance rows approximated. Beacon consumes an export instead of
maintaining a worse copy. See beacon-internal
docs/2026-08-02-beacon-verity-boundary.md.

What is deliberately kept: eval_items (the gold store), suites and
eval_item_suites (the container runs are compared within), and traces (the
execution trace, which is a different thing from a production trace and is what
verifying a runner's declared config depends on).

Data note, checked before writing this: production_traces was empty in the dev
database, and provenance_events held 10 rows from the BIRD ingest whose facts
-- item, dataset version, question id, source -- are all also recorded on the
eval items themselves. The audit rows are derivable, not unique.

Revision ID: 0013_drop_curation
Revises: 0012_verdict_metric
Create Date: 2026-08-02 00:00:00
"""

from __future__ import annotations

from alembic import op

revision = "0013_drop_curation"
down_revision = "0012_verdict_metric"
branch_labels = None
depends_on = None

_TABLES = ("provenance_events", "production_traces")


def upgrade() -> None:
    """Drop the curation tables and their RLS policies."""
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_visible ON {table};")
        op.execute(f"ALTER TABLE IF EXISTS {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE IF EXISTS {table} DISABLE ROW LEVEL SECURITY;")
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")


def downgrade() -> None:
    """Not reversible.

    These tables were dropped because the product no longer curates eval data;
    recreating empty shells would imply a capability that no longer exists, and
    the rows themselves are gone. Restore from a backup taken before 0013 if the
    decision is reversed.
    """
    raise NotImplementedError(
        "0013 is not reversible: curation moved to the semantic layer. "
        "Restore from a backup taken before this migration."
    )
