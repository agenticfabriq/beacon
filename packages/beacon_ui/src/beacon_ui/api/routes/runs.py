"""Project run API routes."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from statistics import median
from typing import Annotated, cast
from uuid import UUID  # noqa: TC003

from beacon_ablation.metrics import (
    gradeable_results,
    median_total_tokens,
    min_attempts_per_task,
    suite_pass_at_k,
    suite_pass_hat_k,
)
from beacon_iam.permissions import Permission
from beacon_storage.config_identity import config_digest as compute_config_digest
from beacon_storage.config_identity import config_label_of, model_id_of
from beacon_storage.errors import ConflictingSolutionDeclarationError
from beacon_storage.ids import uuid7
from beacon_storage.models.runs import HarnessMode, Run, RunStatus, VerdictOutcome
from beacon_storage.models.solutions import Solution  # noqa: TC002
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.run import (
    RunCreate,
    RunInvalidateIn,
    RunOut,
    RunSummaryOut,
    SolutionDeclarationIn,
)

router = APIRouter(prefix="/v1", tags=["runs"])


def _run_status(run: Run) -> str:
    status_value = run.status.value if isinstance(run.status, RunStatus) else str(run.status)
    if status_value == RunStatus.PENDING.value:
        return "queued"
    return status_value


def _run_mode(run: Run) -> str:
    return run.mode.value if isinstance(run.mode, HarnessMode) else str(run.mode)


def _parse_status_filter(value: str | None) -> RunStatus | None:
    if value is None or value == "":
        return None
    aliases = {
        "queued": RunStatus.PENDING,
        "success": RunStatus.COMPLETED,
    }
    if value in aliases:
        return aliases[value]
    try:
        return RunStatus(value)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unknown run status {value!r}",
        ) from exc


def _summary(run: Run, session: Session) -> RunSummaryOut:
    """Summarise one run using the real pass@k statistics.

    A run records one attempt per item (``HarnessRunner`` fans out a single
    ``_ItemTask`` per item at ``attempt_idx=pass_idx``), so ``pass@3``/``pass@5``
    and ``pass^3`` are usually not answerable from a single run and come back as
    ``None`` rather than a stand-in for ``pass@1``. Repeated attempts live in
    sibling runs sharing a ``parent_sweep_id``.
    """
    results = ResultRepo(session).list_for_run(run.id)
    item_ids = {result.item_id for result in results}
    n_items = len(item_ids)
    if n_items == 0:
        return RunSummaryOut(n_items=0)

    graded = gradeable_results(results)
    n_errors = n_items - len({result.item_id for result in graded})
    n_deferred = len({r.item_id for r in graded if r.outcome == VerdictOutcome.DEFER})
    latencies = [result.runtime_ms for result in results]

    return RunSummaryOut(
        pass_at_1=_pass_at_k(graded, k=1),
        pass_at_3=_pass_at_k(graded, k=3),
        pass_at_5=_pass_at_k(graded, k=5),
        pass_hat_3=_pass_hat_k(graded, k=3),
        median_tokens=median_total_tokens(results),
        median_latency_ms=float(median(latencies)) if latencies else None,
        n_items=n_items,
        n_errors=n_errors,
        n_deferred=n_deferred,
    )


def _pass_at_k(graded: Sequence[object], *, k: int) -> float | None:
    """Return suite pass@k, or None when some task has fewer than k attempts."""
    if min_attempts_per_task(graded) < k:
        return None
    return suite_pass_at_k(graded, k=k)


def _pass_hat_k(graded: Sequence[object], *, k: int) -> float | None:
    """Return suite pass^k, or None when some task has fewer than k attempts."""
    if min_attempts_per_task(graded) < k:
        return None
    return suite_pass_hat_k(graded, k=k)


def _run_out(run: Run, session: Session) -> RunOut:
    return RunOut(
        run_id=run.id,
        solution_id=run.solution_id,
        suite_id=run.suite_id,
        mode=_run_mode(run),
        status=_run_status(run),
        started_at=run.started_at,
        completed_at=run.completed_at,
        parent_sweep_id=run.parent_sweep_id,
        sweep_arm=run.sweep_arm,
        model_id=run.model_id,
        config_label=run.config_label,
        config_digest=run.config_digest,
        invalidated_at=run.invalidated_at,
        created_at=run.created_at,
        invalidation_reason=run.invalidation_reason,
        summary=_summary(run, session),
    )


def _dataset_version_for_suite(suite_metadata: dict[str, object]) -> str:
    value = suite_metadata.get("dataset_version")
    if isinstance(value, str) and value:
        return value
    return "v1"


def _declared_solution(
    session: Session,
    *,
    declaration: SolutionDeclarationIn,
    team_id: UUID,
    actor_id: UUID,
) -> Solution:
    """Register what the runner declared, or reuse the identical registration.

    A divergent re-declaration is a 409 rather than an overwrite: the layers a
    version declares are what attribution ablates, so rewriting them would
    reinterpret every comparison already drawn against that version.
    """
    try:
        solution, _created = SolutionRepo(session).ensure_declared(
            team_id=team_id,
            solution_id=declaration.solution_id,
            version=declaration.version,
            summary=declaration.summary,
            supported_modes=declaration.supported_modes,
            layers=list(declaration.layers),
            created_by=actor_id,
        )
    except ConflictingSolutionDeclarationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return solution


@router.post(
    "/suites/{suite_id}/runs",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Kick off a run",
)
@requires(Permission.EVAL_RUN)
def kick_off_run(
    suite_id: UUID,
    body: RunCreate,
    actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_RUN, scope_kind="suite")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> RunOut:
    """Register a run, for a catalogued solution or one the runner declares."""
    suite = SuiteRepo(session).get(suite_id)
    assert suite is not None  # the permission dependency 404s first

    if body.solution is not None:
        solution = _declared_solution(
            session, declaration=body.solution, team_id=suite.team_id, actor_id=actor.id
        )
    else:
        assert body.solution_id is not None
        found = SolutionRepo(session).get(body.solution_id)
        if found is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"solution {body.solution_id} not found",
            )
        solution = found
        if solution.team_id != suite.team_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "solution belongs to a different team than this benchmark",
            )
    if body.mode.value not in solution.supported_modes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"solution does not support mode {body.mode.value}",
        )

    config: dict[str, object] = dict(cast("dict[str, object]", body.config))
    run = RunRepo(session).create(
        team_id=suite.team_id,
        suite_id=suite.id,
        solution_id=solution.id,
        model_id=model_id_of(config),
        config_label=config_label_of(config),
        config_digest=compute_config_digest(config),
        suite=suite.name,
        dataset_version=_dataset_version_for_suite(suite.suite_metadata),
        mode=body.mode,
        pass_idx=0,
        parent_sweep_id=uuid7(),
        config=config,
        created_by=actor.id,
    )
    return _run_out(run, session)


@router.get(
    "/runs/{run_id}",
    response_model=RunOut,
    summary="Get a run's status and summary metrics",
)
@requires(Permission.EVAL_VIEW)
def get_run(
    run_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="run")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> RunOut:
    """Return a run's status and summary metrics."""
    run = RunRepo(session).get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")
    return _run_out(run, session)


@router.get(
    "/suites/{suite_id}/runs",
    response_model=list[RunOut],
    summary="List runs in this benchmark",
)
@requires(Permission.EVAL_VIEW)
def list_runs(
    suite_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="suite")),
    ],
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    solution_id: UUID | None = None,
    mode: HarnessMode | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    parent_sweep_id: UUID | None = None,
    sweep_arm: str | None = None,
    model_id: str | None = None,
    config_digest: str | None = None,
    include_invalidated: bool = False,
) -> list[RunOut]:
    """List the benchmark's runs, filtered by solution, config, mode or status."""
    runs = RunRepo(session).list_for_suite(
        suite_id,
        limit=limit,
        offset=offset,
        solution_id=solution_id,
        mode=mode,
        status=_parse_status_filter(status_filter),
        model_id=model_id,
        config_digest=config_digest,
        include_invalidated=include_invalidated,
    )
    if parent_sweep_id is not None:
        runs = [run for run in runs if run.parent_sweep_id == parent_sweep_id]
    if sweep_arm is not None:
        runs = [run for run in runs if run.sweep_arm == sweep_arm]
    return [_run_out(run, session) for run in runs]


@router.post(
    "/runs/{run_id}/invalidate",
    response_model=RunOut,
    summary="Retire a run without deleting it",
)
@requires(Permission.EVAL_MANAGE)
def invalidate_run(
    run_id: UUID,
    body: RunInvalidateIn,
    actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_MANAGE, scope_kind="run")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> RunOut:
    """Mark a run invalid, with a reason, so it leaves every aggregate.

    The results are kept. A re-run lands as a new run rather than reviving this
    one, because the ingestion contract refuses to rewrite a graded result.
    """
    repo = RunRepo(session)
    run = repo.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")
    if run.invalidated_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run {run_id} is already invalidated")

    invalidated = repo.invalidate(run_id, user_id=actor.id, reason=body.reason)
    assert invalidated is not None

    # A retired run cannot go on being the thing everything is compared against.
    suite = SuiteRepo(session).get(run.suite_id)
    if suite is not None and suite.baseline_run_id == run_id:
        suite.baseline_run_id = None
    session.commit()
    return _run_out(invalidated, session)


@router.post(
    "/runs/{run_id}/restore",
    response_model=RunOut,
    summary="Undo an invalidation",
)
@requires(Permission.EVAL_MANAGE)
def restore_run(
    run_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_MANAGE, scope_kind="run")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> RunOut:
    """Bring an invalidated run back.

    Invalidating by mistake must not be permanent, or the safe action stops
    being safe and people reach for the database instead. The reference pin is
    not restored: re-pinning is a deliberate act.
    """
    repo = RunRepo(session)
    run = repo.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")
    restored = repo.restore(run_id)
    if restored is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run {run_id} is not invalidated")
    session.commit()
    return _run_out(restored, session)
