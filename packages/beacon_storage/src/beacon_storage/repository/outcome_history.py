"""Record what a result scored and under which derivation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from beacon_storage.ids import uuid7
from beacon_storage.models.outcome_history import Derivation, ResultOutcome

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

_SOURCES = frozenset({"ingest", "regrade", "harness"})


class OutcomeHistoryRepo:
    """Appends outcome history and interns the derivation it was produced by."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def derivation_for(
        self, *, grader: str | None, grader_version: str | None, metric: str | None
    ) -> Derivation:
        """The row for this derivation, created once and reused after.

        A grader and its version travel together; ``metric`` is independent,
        because a grader declaring no metric decides by being the first
        execution verdict and has none to name. Refused here rather than at the
        constraint so the caller sees which field it failed to determine.
        """
        if (grader is None) != (grader_version is None):
            raise ValueError(
                "a grader and its version travel together; got "
                f"grader={grader!r} version={grader_version!r}"
            )
        if grader is None and metric is not None:
            raise ValueError(
                f"metric={metric!r} has no grader to be a reading of; "
                "a metric without a grader is not a derivation"
            )
        # SELECT-then-INSERT is not enough, and this was found by the harness
        # rather than by reasoning: two transactions that both miss the SELECT
        # both INSERT, and one dies on the unique constraint. That is a real
        # production race, not a test artifact -- two concurrent pushes with no
        # deciding grader intern the SAME all-null derivation, and the
        # no-grader case is the common one (every error, timeout and refusal).
        #
        # ON CONFLICT DO NOTHING makes interning idempotent. The constraint is
        # NAMED rather than inferred, and the reason is not the one first
        # recorded here: inference DOES dedupe the all-null row against a
        # NULLS NOT DISTINCT constraint -- measured on Postgres 16, one row
        # survives and no error is raised. Naming it is right for a duller
        # reason: it says which constraint the caller means, so a future index
        # over the same columns cannot silently become the arbiter.
        lookup = sa.select(Derivation).where(
            Derivation.grader.is_(grader) if grader is None else Derivation.grader == grader,
            Derivation.grader_version.is_(grader_version)
            if grader_version is None
            else Derivation.grader_version == grader_version,
            Derivation.metric.is_(metric) if metric is None else Derivation.metric == metric,
        )
        if (existing := self.session.scalar(lookup)) is not None:
            return existing
        self.session.execute(
            pg_insert(Derivation)
            .values(id=uuid7(), grader=grader, grader_version=grader_version, metric=metric)
            .on_conflict_do_nothing(constraint="uq_derivation_key")
        )
        row = self.session.scalar(lookup)
        if row is None:  # pragma: no cover - the insert either wrote it or lost the race
            raise RuntimeError(
                "derivation neither inserted nor found after ON CONFLICT DO NOTHING; "
                f"grader={grader!r} version={grader_version!r} metric={metric!r}"
            )
        return row

    def record(
        self,
        *,
        team_id: UUID,
        result_id: UUID,
        outcome: str,
        source: str,
        grader: str | None = None,
        grader_version: str | None = None,
        metric: str | None = None,
    ) -> ResultOutcome | None:
        """Append this outcome, unless it is already the latest for this result.

        ON CHANGE, which is what keeps the table proportional to what moved
        rather than to what exists: re-recording an unchanged value would add
        127k rows per regrade and tell a reader nothing. The first write is a
        change from nothing and is always recorded.

        Returns the appended row, or None when nothing changed -- so a caller
        can count what it actually wrote instead of assuming.
        """
        if source not in _SOURCES:
            raise ValueError(f"source must be one of {sorted(_SOURCES)}, got {source!r}")
        derivation = self.derivation_for(
            grader=grader, grader_version=grader_version, metric=metric
        )
        latest = self.session.scalar(
            sa.select(ResultOutcome)
            .where(ResultOutcome.result_id == result_id)
            .order_by(ResultOutcome.recorded_at.desc(), ResultOutcome.id.desc())
            .limit(1)
        )
        # Both halves, because a result can return to a value it held before
        # under a NEW derivation, and that is a change worth recording even
        # though the outcome string matches.
        if (
            latest is not None
            and latest.outcome == outcome
            and latest.derivation_id == derivation.id
        ):
            return None
        row = ResultOutcome(
            team_id=team_id,
            result_id=result_id,
            derivation_id=derivation.id,
            outcome=outcome,
            source=source,
        )
        self.session.add(row)
        return row
