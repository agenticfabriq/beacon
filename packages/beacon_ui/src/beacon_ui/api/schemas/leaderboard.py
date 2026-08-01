from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel


class LeaderboardRow(BaseModel):
    team_id: UUID
    team_name: str
    solution_id: UUID
    solution_name: str
    solution_version: str
    # None when some item has fewer than 3 gradeable attempts pooled across
    # runs -- pass@3 is not answerable then, and must not stand in for pass@1.
    pass_at_3: float | None = None
    pass_at_3_ci_low: float | None = None
    pass_at_3_ci_high: float | None = None
    median_tokens: float | None = None
    median_latency_ms: float | None = None
    cost_adjusted_score: float | None = None
    latency_adjusted_score: float | None = None
    n_items: int
    # Items whose every attempt errored; excluded from pass_at_3 above.
    n_errors: int = 0


class LeaderboardOut(BaseModel):
    suite: str
    metric: str
    rows: list[LeaderboardRow]
