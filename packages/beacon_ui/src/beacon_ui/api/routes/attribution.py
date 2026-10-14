"""GET /v1/suites/{suite_id}/attribution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003

from beacon_ablation import per_k_count
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


def _delta_pass_at_3(row: Attribution) -> float | None:
    """The pass@3 delta, or None where the row holds no pass@3 reading -- never a default (B79)."""
    entry = row.delta_pass_at_k.get("3")
    if not isinstance(entry, Mapping):
        return None
    value = entry.get("delta")
    return float(value) if isinstance(value, (Decimal, float, int, str)) else None


def _has_pass_at_3(row: Attribution) -> bool:
    """Whether this row actually holds a pass@3 reading.

    A sweep with K < 3 has no ``"3"`` entry at all -- the engine's headline is
    ``min(3, K)`` and ``run_fs_payments_ablation.py`` defaults to one pass. For
    such a row ``_per_k_metric`` returns its 0.0 default, so ``delta_pass_at_3``
    is published as a confident zero that was never computed.

    The delta is now null on such a row (B79), and the sample counts stay
    absent with it: they are scoped to the headline k, so a real n beside a
    missing delta would still read as a finding nobody made (B64).
    """
    return isinstance(row.delta_pass_at_k.get("3"), Mapping)


def _headline_k(row: Attribution) -> int | None:
    """The k the row's CI and p-values describe: the engine's headline, min(3, K).

    K is the number of passes the sweep ran, and the engine writes one
    ``delta_pass_at_k`` entry per k from 1 to K -- so K is the largest key.
    """
    ks = [int(key) for key in row.delta_pass_at_k if str(key).isdigit()]
    return min(3, max(ks)) if ks else None


def _per_k_count(row: Attribution, key: str, column: int | None) -> int | None:
    """A count from the pass@3 entry, falling back to the promoted column.

    The reading itself is ``beacon_ablation.per_k_count``, shared with the
    sweep reports so that "what an absent count means" is answered in one
    place. This adds the column fallback: the JSONB entry is preferred for the
    same reason the deltas are read from there -- it is the value computed for
    THIS k -- and either may be absent on a row written before the counts
    existed, which stays None rather than becoming 0.
    """
    from_entry = per_k_count(row.delta_pass_at_k.get("3"), key)
    return column if from_entry is None else from_entry


def _by_effect(layer: AttributionLayerOut) -> tuple[bool, float]:
    """Largest effect first; a row with no pass@3 delta last, never sorted as a zero (B79)."""
    if layer.delta_pass_at_3 is None:
        return (True, 0.0)
    return (False, -abs(layer.delta_pass_at_3))


def _layers(rows: Iterable[Attribution]) -> list[AttributionLayerOut]:
    """The snapshot's layers, in the order the endpoint publishes them."""
    return sorted((_layer_out(row) for row in rows), key=_by_effect)


def _layer_out(row: Attribution) -> AttributionLayerOut:
    at_3 = _has_pass_at_3(row)
    return AttributionLayerOut(
        layer=row.layer_name,
        delta_pass_at_3=_delta_pass_at_3(row),
        k=_headline_k(row),
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
    layers = _layers(rows)
    return AttributionSnapshotOut(
        solution_id=sut,
        suite_id=suite_id,
        supported=True,
        computed_at=max((row.created_at for row in rows), default=None),
        layers=layers,
    )
