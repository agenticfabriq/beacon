"""GET /v1/suites/{suite_id}/attribution."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.attributions import AttributionRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.attribution import AttributionLayerOut, AttributionSnapshotOut

if TYPE_CHECKING:
    from beacon_storage.models.attribution import Attribution

router = APIRouter(prefix="/v1", tags=["attribution"])


def _to_float(value: object, default: float) -> float:
    if isinstance(value, (Decimal, float, int, str)):
        return float(value)
    return default


def _per_k_metric(row: Attribution, key: str, default: float) -> float:
    entry = row.delta_pass_at_k.get("3")
    if isinstance(entry, Mapping):
        return _to_float(entry.get(key), default)
    return default


def _has_pass_at_3(row: Attribution) -> bool:
    """Whether this row actually holds a pass@3 reading.

    A sweep with K < 3 has no ``"3"`` entry at all -- the engine's headline is
    ``min(3, K)`` and ``run_fs_payments_ablation.py`` defaults to one pass. For
    such a row ``_per_k_metric`` returns its 0.0 default, so ``delta_pass_at_3``
    is published as a confident zero that was never computed.

    That is not fixed here, but the sample counts must not CORROBORATE it. The
    per-arm counts are scoped to the headline k, so publishing a real n beside
    a fabricated delta would turn a detectable fiction into a plausible
    finding -- the precise failure B64 exists to prevent. Absent instead.
    """
    return isinstance(row.delta_pass_at_k.get("3"), Mapping)


def _per_k_count(row: Attribution, key: str, column: int | None) -> int | None:
    """A count from the pass@3 entry, falling back to the promoted column.

    The JSONB entry is preferred for the same reason the deltas are read from
    there: it is the value computed for THIS k. The column is the headline
    promotion, and either may be absent on a row written before the counts
    existed -- which stays None rather than becoming 0, since 0 would assert
    that nothing was excluded.
    """
    entry = row.delta_pass_at_k.get("3")
    if isinstance(entry, Mapping):
        value = entry.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return column


def _layer_out(row: Attribution) -> AttributionLayerOut:
    at_3 = _has_pass_at_3(row)
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
        # `n_items_submitted` is k-independent -- how many items the sweep was
        # asked to run -- so it is honest on any row. The other three are
        # scoped to a k, and are withheld when this row carries no pass@3.
        n_items_submitted=row.n_items_submitted,
        n_compared=_per_k_count(row, "n_compared", row.n_compared) if at_3 else None,
        n_baseline_excluded=row.n_baseline_excluded if at_3 else None,
        n_ablated_excluded=row.n_ablated_excluded if at_3 else None,
    )


@router.get(
    "/suites/{suite_id}/attribution",
    response_model=AttributionSnapshotOut,
    summary="Get the latest attribution snapshot for a solution and suite",
)
@requires(Permission.EVAL_VIEW)
def get_suite_attribution(
    suite_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="suite")),
    ],
    session: Annotated[Session, Depends(get_session)],
    sut: Annotated[UUID, Query(description="Solution UUID")],
) -> AttributionSnapshotOut:
    """Return the latest per-layer attribution snapshot for a solution and benchmark."""
    suite_record = SuiteRepo(session).get(suite_id)
    assert suite_record is not None  # the permission dependency 404s first

    solution = SolutionRepo(session).get(sut)
    if solution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"solution {sut} not found")
    if solution.team_id != suite_record.team_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "solution belongs to a different team than this benchmark",
        )

    if not solution.layers:
        return AttributionSnapshotOut(
            solution_id=sut,
            suite_id=suite_id,
            supported=False,
            computed_at=None,
            layers=[],
        )

    rows = AttributionRepo(session).latest_for(
        team_id=suite_record.team_id,
        solution_id=sut,
        suite=suite_record.name,
    )
    layers = sorted((_layer_out(row) for row in rows), key=lambda row: -abs(row.delta_pass_at_3))
    return AttributionSnapshotOut(
        solution_id=sut,
        suite_id=suite_id,
        supported=True,
        computed_at=max((row.created_at for row in rows), default=None),
        layers=layers,
    )
