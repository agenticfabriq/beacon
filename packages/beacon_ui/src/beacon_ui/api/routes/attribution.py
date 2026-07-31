"""GET /v1/projects/{project_id}/attribution."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.attributions import AttributionRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.attribution import AttributionLayerOut, AttributionSnapshotOut

if TYPE_CHECKING:
    from beacon_storage.models.attribution import Attribution

router = APIRouter(prefix="/v1/projects", tags=["attribution"])


def _to_float(value: object, default: float) -> float:
    if isinstance(value, (Decimal, float, int, str)):
        return float(value)
    return default


def _per_k_metric(row: Attribution, key: str, default: float) -> float:
    entry = row.delta_pass_at_k.get("3")
    if isinstance(entry, Mapping):
        return _to_float(entry.get(key), default)
    return default


def _layer_out(row: Attribution) -> AttributionLayerOut:
    return AttributionLayerOut(
        layer=row.layer_name,
        delta_pass_at_3=_per_k_metric(row, "delta", 0.0),
        ci_low=_per_k_metric(row, "ci_low", float(row.ci_low)),
        ci_high=_per_k_metric(row, "ci_high", float(row.ci_high)),
        mcnemar_p=float(row.mcnemar_p),
        bh_p=float(row.bh_adjusted_p if row.bh_adjusted_p is not None else row.mcnemar_p),
        median_token_delta_pct=(
            None if row.token_delta_pct is None else float(row.token_delta_pct)
        ),
    )


@router.get(
    "/{project_id}/attribution",
    response_model=AttributionSnapshotOut,
    summary="Get the latest attribution snapshot for a solution and suite",
)
@requires(Permission.PROJECT_VIEW)
def get_project_attribution(
    project_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.PROJECT_VIEW, scope_kind="project")),
    ],
    session: Annotated[Session, Depends(get_session)],
    sut: Annotated[UUID, Query(description="Solution UUID")],
    suite: Annotated[UUID, Query(description="Suite UUID")],
) -> AttributionSnapshotOut:
    """Return the latest per-layer attribution snapshot for a solution and suite."""
    project = ProjectRepo(session).get(project_id)
    assert project is not None

    solution = SolutionRepo(session).get(sut)
    if solution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"solution {sut} not found")
    if solution.team_id != project.team_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "solution belongs to a different team than this project",
        )

    if not solution.layers:
        return AttributionSnapshotOut(
            project_id=project_id,
            solution_id=sut,
            suite_id=suite,
            supported=False,
            computed_at=None,
            layers=[],
        )

    suite_record = SuiteRepo(session).get(suite)
    if suite_record is None or suite_record.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"suite {suite} not found")

    rows = AttributionRepo(session).latest_for(
        project_id=project_id,
        solution_id=sut,
        suite=suite_record.name,
    )
    layers = sorted((_layer_out(row) for row in rows), key=lambda row: -abs(row.delta_pass_at_3))
    return AttributionSnapshotOut(
        project_id=project_id,
        solution_id=sut,
        suite_id=suite,
        supported=True,
        computed_at=max((row.created_at for row in rows), default=None),
        layers=layers,
    )
