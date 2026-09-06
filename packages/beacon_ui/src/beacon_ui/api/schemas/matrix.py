"""Schemas for the results matrix: one row per system · version · model · config."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
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
    # The retrieval depth this configuration ran at, when the runner recorded
    # one. It is part of the digest, so it already splits rows -- this is what
    # lets a row SAY which depth it is instead of differing invisibly. None
    # means unrecorded, which is not the same as any particular depth.
    retrieval_k: int | None = None
    # The sweep arms pooled into this row, comma-joined. A run's arm is part
    # of its identity; when config_label is empty this is the readable name,
    # and more than one arm here means the row pools across arms.
    arms: str | None = None
    # The lowest and highest per-run headline rate pooled into this row, when
    # it pools more than one run. A single number over repetitions that
    # disagree reads as a quantity; this is the error bar the row already had
    # and never showed. None for a single run -- one measurement has no spread.
    ex_rate_min: float | None = None
    ex_rate_max: float | None = None
    # The engine the runner declared it executed against; a facet, not a verdict.
    engine: str | None = None
    n_runs: int
    n_graded: int
    n_errors: int
    ex_rate: float | None = None
    # BIRD-comparable exact match: passes whose SQL the runner verified against
    # the gold's engine. None unless the runner supplied portability flags.
    ex_target_engine_rate: float | None = None
    # The strict reading: exact result-set match by beacon's own grader,
    # suite-independent. EX stays the benchmark-headline number (strict for
    # BIRD, tolerant for Spider -- each benchmark defines its own); this
    # column means the same thing on every row. None when no verdict carries
    # the metric, which must not read as 0%; a metric that WAS read and matched
    # nothing is 0.0, which must not read as None.
    exact_rate: float | None = None
    # The tolerant reading: right data, shape-tolerant (mnemiq's CORRECT_FACTS,
    # computed by beacon's own grader). None when no verdict carries the metric
    # -- runs graded before the grader emitted it -- which must not read as 0%;
    # read and matched nothing is 0.0, which must not read as None.
    got_facts_rate: float | None = None
    # What `got_facts_rate` is a rate OVER, because it is not one question.
    # `condition_cols` restricts the tolerant reading to the benchmark's scored
    # columns, so for a third of spider2 this metric asks about a strict subset
    # of gold -- one item scored PASS on its month column while its value was
    # 6x off (B72). Summing that with full-table readings produces a number
    # that cannot be compared across rows, and the rate alone never said so.
    #
    # Counted directly, not derived: subset + full + unknown == the scored
    # total, and a reader who has to subtract to learn the composition is one
    # arithmetic slip from B68's 150% row.
    #
    # `unknown` is verdicts written before the grader declared its scope. It is
    # NOT "the whole table was compared" -- that distinction is the whole point
    # of recording a positive marker rather than inferring full from a missing
    # key.
    #
    # The total is exposed too. Publishing the parts without the whole leaves a
    # reader adding three numbers to recover the denominator the rate is over,
    # which is the arithmetic this breakdown exists to spare them (B57).
    n_got_facts_scored: int = 0
    n_got_facts_subset_scored: int = 0
    n_got_facts_full_scored: int = 0
    n_got_facts_scope_unknown: int = 0
    defer_rate: float | None = None
    wrong_rate: float | None = None
    # Present only under an as-of. `current` is this row's live values, and
    # `affected` says whether the selected event touched any of its results --
    # read from the event's own records, not inferred from the rates matching,
    # because two changes can cancel and "the number is the same" is a
    # different claim from "this event did not touch it".
    current: MatrixCurrentOut | None = None
    affected: bool | None = None

    median_tokens: float | None = None
    median_runtime_ms: float | None = None


class MatrixCurrentOut(BaseModel):
    """The same row's CURRENT values, carried beside a rewound one.

    Present only when ``as_of_event`` is set. Five rates rather than one,
    because a single delta on the headline leaves the rest to change in
    silence -- measured on the first real event: EX, wrong, exact and
    got-facts each moved in 28 of 28 rows, and only EX had a delta.
    ``defer_rate`` is here to complete the outcome triple; a regrade cannot
    move it, since it re-derives only results already PASS or FAIL.

    ``n_graded`` too: an ``ERROR`` crossing into the graded set moves the
    denominator, not just the numerator. It did not happen in that event, and
    a reader cannot be asked to assume it never will.
    """

    model_config = ConfigDict(extra="forbid")

    ex_rate: float | None = None
    defer_rate: float | None = None
    wrong_rate: float | None = None
    exact_rate: float | None = None
    got_facts_rate: float | None = None
    n_graded: int


class MatrixAsOfOut(BaseModel):
    """Which point the rows were read at, and what that did NOT rewind."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    recorded_at: datetime
    grader: str
    grader_version: str
    headline_metric: str

    # Stated in the payload, not left to the UI's copy. A selector that
    # rewound grading while the run set moved underneath would be a new way to
    # publish a number nobody can reproduce, so the response names its own
    # limits: only recorded grading changes, and only back to the first
    # recorded event.
    rewinds: str = (
        "Recorded grading changes only. A rate that moved because a run landed or "
        "was invalidated is NOT rewound: the matrix pools every valid run with no "
        "time bound. Outcomes for results no recorded event touched are shown at "
        "their current value."
    )


class MatrixOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[MatrixRowOut]
    as_of: MatrixAsOfOut | None = None
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
    gold_sql: str | None = None


class SuiteItemListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SuiteItemRowOut]
    total: int
    difficulty_counts: dict[str, int]
    source_counts: dict[str, int]
