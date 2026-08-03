"""Schemas for the results matrix: one row per system · version · model · config."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict


class MatrixRowOut(BaseModel):
    """One configuration's aggregate across its valid runs.

    Rates follow the register's semantics: ERROR leaves the denominator
    (an outage is not a wrong answer), DEFER stays in it (declining is an
    outcome), and a rate over nothing gradeable is None, never 0.0.
    """

    model_config = ConfigDict(extra="forbid")

    solution_id: UUID
    solution_name: str
    solution_version: str
    model_id: str | None = None
    config_label: str | None = None
    config_digest: str | None = None
    n_runs: int
    n_graded: int
    n_errors: int
    ex_rate: float | None = None
    # The tolerant reading: right data, shape-tolerant (mnemiq's CORRECT_FACTS,
    # computed by beacon's own grader). None when no verdict carries the metric
    # -- runs graded before the grader emitted it -- which must not read as 0%.
    got_facts_rate: float | None = None
    defer_rate: float | None = None
    wrong_rate: float | None = None
    median_tokens: float | None = None
    median_runtime_ms: float | None = None


class MatrixOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[MatrixRowOut]
    # Facet counts over the whole selection, so a difficulty-filtered view
    # still shows what it is a slice of.
    difficulty_counts: dict[str, int]


class SuiteItemRowOut(BaseModel):
    """One question in a benchmark, at listing density."""

    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    question: str
    difficulty: str | None = None
    database: str | None = None
    source: str | None = None
    tolerance: dict[str, object] | None = None
    has_gold_sql: bool


class SuiteItemListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SuiteItemRowOut]
    total: int
    difficulty_counts: dict[str, int]
    source_counts: dict[str, int]
