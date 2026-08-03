"""Cross-team leaderboard API routes for shared benchmark suites."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING, Annotated, Literal

from beacon_ablation.metrics import (
    gradeable_results,
    min_attempts_per_task,
    suite_pass_at_k,
)
from beacon_storage.models.eval_items import EvalItem
from beacon_storage.models.runs import Result, Run, RunStatus, VerdictOutcome
from beacon_storage.models.solutions import Solution
from beacon_storage.models.tenancy import Team, User  # noqa: TC002
from beacon_storage.rls import clear_current_user
from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_current_user, get_session
from beacon_ui.api.schemas.leaderboard import LeaderboardOut, LeaderboardRow

router = APIRouter(prefix="/v1/leaderboards", tags=["leaderboards"])

_K_LEADERBOARD = 3

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True)
class _ResultRow:
    team_id: UUID
    team_name: str
    solution_id: UUID
    solution_name: str
    solution_version: str
    item_id: str
    attempt_idx: int
    tokens_input: int
    tokens_output: int
    runtime_ms: int
    outcome: VerdictOutcome | str | None


def _safe_ratio(numerator: float, denominator: float | None) -> float | None:
    if denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _shared_result_rows(session: Session, *, suite: str) -> list[_ResultRow]:
    """Return results joined to **shared** eval items for ``suite``.

    ``EvalItem.team_id IS NULL`` restricts the join to items every team can see.
    This is deliberate -- a cross-team leaderboard must rank on a common set of
    items, and a team's private items are neither visible nor comparable. The
    consequence is that runs over team-scoped items appear in the runs panel but
    never on a leaderboard, which used to happen with no indication why; the
    response now reports ``scope`` and ``excluded_team_scoped_items`` so the
    omission is legible rather than silent.
    """
    stmt = (
        select(
            Run.team_id,
            Team.name.label("team_name"),
            Run.solution_id,
            Solution.solution_id.label("solution_name"),
            Solution.version.label("solution_version"),
            Result.item_id,
            Result.attempt_idx,
            Result.tokens_input,
            Result.tokens_output,
            Result.runtime_ms,
            Result.outcome,
        )
        .join(Result, Result.run_id == Run.id)
        .join(Solution, Solution.id == Run.solution_id)
        .join(Team, Team.id == Run.team_id)
        .join(
            EvalItem,
            (cast(EvalItem.item_id, String) == Result.item_id)
            & (EvalItem.valid_to.is_(None))
            & (EvalItem.team_id.is_(None))
            & (EvalItem.suite == suite),
        )
        # An invalidated run is a retired experiment: it keeps its results but
        # must not rank, or invalidating it changes nothing anyone can see.
        .where(
            Run.suite == suite,
            Run.status == RunStatus.COMPLETED,
            Run.invalidated_at.is_(None),
        )
    )
    return [
        _ResultRow(
            team_id=row.team_id,
            team_name=row.team_name,
            solution_id=row.solution_id,
            solution_name=row.solution_name,
            solution_version=row.solution_version,
            item_id=row.item_id,
            attempt_idx=row.attempt_idx,
            tokens_input=row.tokens_input,
            tokens_output=row.tokens_output,
            runtime_ms=row.runtime_ms,
            outcome=row.outcome,
        )
        for row in session.execute(stmt)
    ]


def _rows_for_metric(
    result_rows: list[_ResultRow],
    *,
    metric: Literal["cost", "latency"],
    limit: int,
) -> list[LeaderboardRow]:
    groups: dict[tuple[UUID, str, UUID, str, str], list[_ResultRow]] = {}
    for row in result_rows:
        key = (
            row.team_id,
            row.team_name,
            row.solution_id,
            row.solution_name,
            row.solution_version,
        )
        groups.setdefault(key, []).append(row)

    rows: list[LeaderboardRow] = []
    for (
        team_id,
        team_name,
        solution_id,
        solution_name,
        solution_version,
    ), group_rows in groups.items():
        item_ids = {row.item_id for row in group_rows}
        if not item_ids:
            continue
        # Attempts are pooled across every completed run of this suite for the
        # solution, so k>1 is answerable here in a way it is not for one run.
        graded = gradeable_results(group_rows)
        n_errors = len(item_ids) - len({row.item_id for row in graded})
        pass_at_3 = (
            suite_pass_at_k(graded, k=_K_LEADERBOARD)
            if min_attempts_per_task(graded) >= _K_LEADERBOARD
            else None
        )
        token_totals = [row.tokens_input + row.tokens_output for row in group_rows]
        latencies = [row.runtime_ms for row in group_rows]
        median_tokens = float(median(token_totals)) if token_totals else None
        median_latency_ms = float(median(latencies)) if latencies else None
        cost_adjusted = (
            _safe_ratio(pass_at_3, median_tokens)
            if metric == "cost" and pass_at_3 is not None
            else None
        )
        latency_adjusted = (
            _safe_ratio(pass_at_3, median_latency_ms)
            if metric == "latency" and pass_at_3 is not None
            else None
        )
        rows.append(
            LeaderboardRow(
                team_id=team_id,
                team_name=team_name,
                solution_id=solution_id,
                solution_name=solution_name,
                solution_version=solution_version,
                pass_at_3=pass_at_3,
                median_tokens=median_tokens,
                median_latency_ms=median_latency_ms,
                cost_adjusted_score=cost_adjusted,
                latency_adjusted_score=latency_adjusted,
                n_items=len(item_ids),
                n_errors=n_errors,
            )
        )

    score_field = "cost_adjusted_score" if metric == "cost" else "latency_adjusted_score"
    rows.sort(
        key=lambda row: (
            getattr(row, score_field) is None,
            -(getattr(row, score_field) or 0.0),
            row.pass_at_3 is None,
            -(row.pass_at_3 or 0.0),
            -row.n_items,
            row.team_name,
            row.solution_name,
            row.solution_version,
        )
    )
    return rows[:limit]


def _leaderboard(
    session: Session,
    *,
    suite: str,
    metric: Literal["cost", "latency"],
    limit: int,
) -> LeaderboardOut:
    # Shared-suite leaderboards are authenticated aggregate views, not team-scoped run views.
    clear_current_user(session)
    rows = _rows_for_metric(_shared_result_rows(session, suite=suite), metric=metric, limit=limit)
    return LeaderboardOut(
        suite=suite,
        metric=metric,
        rows=rows,
        excluded_team_scoped_items=_team_scoped_item_count(session, suite=suite),
    )


def _team_scoped_item_count(session: Session, *, suite: str) -> int:
    """Count items in ``suite`` a leaderboard cannot rank because they are private."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(EvalItem)
            .where(
                EvalItem.suite == suite,
                EvalItem.valid_to.is_(None),
                EvalItem.team_id.is_not(None),
            )
        )
        or 0
    )


@router.get(
    "/cost",
    response_model=LeaderboardOut,
    summary="Cost-adjusted leaderboard on a shared suite",
)
def cost_leaderboard(
    suite: Annotated[str, Query(description="Suite name, e.g. bird_minidev_v2")],
    session: Annotated[Session, Depends(get_session)],
    _actor: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> LeaderboardOut:
    """Return the cost-adjusted leaderboard for a shared benchmark suite."""
    return _leaderboard(session, suite=suite, metric="cost", limit=limit)


@router.get(
    "/latency",
    response_model=LeaderboardOut,
    summary="Latency-adjusted leaderboard on a shared suite",
)
def latency_leaderboard(
    suite: Annotated[str, Query(description="Suite name, e.g. bird_minidev_v2")],
    session: Annotated[Session, Depends(get_session)],
    _actor: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> LeaderboardOut:
    """Return the latency-adjusted leaderboard for a shared benchmark suite."""
    return _leaderboard(session, suite=suite, metric="latency", limit=limit)
