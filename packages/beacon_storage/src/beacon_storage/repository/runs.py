from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from beacon_storage.errors import DuplicateRunError
from beacon_storage.models.runs import HarnessMode, Run, RunStatus

_UNIQUE_RUN_CONSTRAINT = "uq_run_suite_pass"

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class RunRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        team_id: UUID,
        solution_id: UUID,
        suite_id: UUID,
        suite: str,
        dataset_version: str,
        mode: HarnessMode,
        pass_idx: int,
        config: dict[str, object],
        model_id: str | None = None,
        config_label: str | None = None,
        config_digest: str | None = None,
        created_by: UUID,
        parent_sweep_id: UUID | None = None,
        sweep_arm: str | None = None,
    ) -> Run:
        """Create a pending run for the given solution/suite and return it.

        Run identity is ``(suite_id, solution_id, dataset_version,
        mode, pass_idx, parent_sweep_id, sweep_arm)`` and the constraint treats
        NULLs as equal, so **repeating a run needs something to distinguish it**.
        Pick deliberately:

        * a fresh ``parent_sweep_id`` (``uuid7()``) groups the runs of one
          sweep and makes each invocation distinct -- what the API and the
          benchmark scripts do;
        * a different ``pass_idx`` records repeated attempts at the same suite;
        * ``sweep_arm`` distinguishes the arms within one sweep.

        Leaving all of them at their defaults twice raises
        :class:`~beacon_storage.errors.DuplicateRunError` rather than an
        ``IntegrityError`` from deep inside persistence.
        """
        run = Run(
            team_id=team_id,
            solution_id=solution_id,
            suite_id=suite_id,
            suite=suite,
            dataset_version=dataset_version,
            mode=mode,
            pass_idx=pass_idx,
            parent_sweep_id=parent_sweep_id,
            sweep_arm=sweep_arm,
            config=config,
            model_id=model_id,
            config_label=config_label,
            config_digest=config_digest,
            status=RunStatus.PENDING,
            created_by=created_by,
        )
        self.session.add(run)
        try:
            self.session.flush()
        except IntegrityError as exc:
            if _UNIQUE_RUN_CONSTRAINT not in str(exc.orig):
                raise
            raise DuplicateRunError(
                f"a run already exists for suite={suite_id} solution={solution_id} "
                f"suite={suite!r} dataset_version={dataset_version!r} mode={mode.value} "
                f"pass_idx={pass_idx} parent_sweep_id={parent_sweep_id} "
                f"sweep_arm={sweep_arm!r}. Pass a fresh parent_sweep_id (uuid7()) to "
                "record a new invocation, a different pass_idx for a repeat attempt, "
                "or a sweep_arm to distinguish arms of one sweep."
            ) from exc
        return run

    def get(self, run_id: UUID) -> Run | None:
        """Return the run with id ``run_id`` or None."""
        return self.session.get(Run, run_id)

    def invalidate(self, run_id: UUID, *, user_id: UUID, reason: str) -> Run | None:
        """Retire a run, recording who and why. Returns None if already invalid.

        The reason is required. An invalidation with no reason is a deletion
        with extra steps, and the point of keeping the row is the explanation.
        """
        if not reason.strip():
            raise ValueError("an invalidation needs a reason")
        run = self.session.get(Run, run_id)
        if run is None or run.invalidated_at is not None:
            return None
        run.invalidated_at = datetime.now(UTC)
        run.invalidated_by = user_id
        run.invalidation_reason = reason.strip()
        self.session.flush()
        return run

    def restore(self, run_id: UUID) -> Run | None:
        """Undo an invalidation. Returns None if the run was not invalidated.

        Invalidating by mistake must not be permanent, or the safe action stops
        being safe and people reach for the database instead.
        """
        run = self.session.get(Run, run_id)
        if run is None or run.invalidated_at is None:
            return None
        run.invalidated_at = None
        run.invalidated_by = None
        run.invalidation_reason = None
        self.session.flush()
        return run

    def mark_running(self, run_id: UUID) -> None:
        """Set the run to ``RUNNING`` and stamp ``started_at``."""
        run = self.session.get(Run, run_id)
        if run is None:
            return
        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        self.session.flush()

    def mark_completed(self, run_id: UUID) -> None:
        """Set the run to ``COMPLETED`` and stamp ``completed_at``."""
        run = self.session.get(Run, run_id)
        if run is None:
            return
        run.status = RunStatus.COMPLETED
        run.completed_at = datetime.now(UTC)
        self.session.flush()

    def mark_failed(self, run_id: UUID, error: str) -> None:
        """Set the run to ``FAILED``, persist a truncated ``error``, and stamp completion."""
        run = self.session.get(Run, run_id)
        if run is None:
            return
        run.status = RunStatus.FAILED
        run.error = error[:4000]
        run.completed_at = datetime.now(UTC)
        self.session.flush()

    def list_for_suite(
        self,
        suite_id: UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
        solution_id: UUID | None = None,
        mode: HarnessMode | None = None,
        status: RunStatus | None = None,
        model_id: str | None = None,
        config_digest: str | None = None,
        include_invalidated: bool = False,
    ) -> list[Run]:
        """Return runs in ``suite_id`` filtered by optional facets, newest first.

        Invalidated runs are excluded unless asked for: a retired experiment
        that still showed up in the default listing would go on being read as a
        result.
        """
        stmt = select(Run).where(Run.suite_id == suite_id)
        if not include_invalidated:
            stmt = stmt.where(Run.invalidated_at.is_(None))
        if solution_id is not None:
            stmt = stmt.where(Run.solution_id == solution_id)
        if mode is not None:
            stmt = stmt.where(Run.mode == mode)
        if status is not None:
            stmt = stmt.where(Run.status == status)
        if model_id is not None:
            stmt = stmt.where(Run.model_id == model_id)
        if config_digest is not None:
            stmt = stmt.where(Run.config_digest == config_digest)
        stmt = stmt.order_by(Run.created_at.desc()).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt))
