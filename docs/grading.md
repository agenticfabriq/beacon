# The grading contract

One grader, every benchmark. Differences between benchmarks live in item data
(how many accepted golds, which columns count, row-order opinion, tolerance) —
never in grader forks. This document is the agreement; `beacon_graders` is the
implementation; mnemiq's `grade.py` points here instead of restating it.
Agreed 2026-08-07 between beacon and mnemiq (mnemiq converged at its commit
`91485fa`).

## The three readings

Every graded execution gets two verdicts from `ResultSetMatchGrader`, and the
matrix shows three columns:

| Column | Meaning | Defined by |
|---|---|---|
| **EX** | the benchmark's own headline rule (strict for BIRD, tolerant for Spider) | each benchmark |
| **exact** | exact result-set match | beacon, same on every suite |
| **got-facts** | the gold's information is present | beacon, same on every suite |

EX is leaderboard-comparable and suite-defined; the other two mean the same
thing on every row. When EX equals one of them, that coincidence is
information about how the benchmark grades, not a bug: a strict benchmark's
EX sits on exact, a tolerant one's on got-facts.

Suites DECLARE their headline metric (`headline_metric` in suite metadata:
`exact_match` for BIRD, `got_facts` for Spider 2.0-lite), and every scored
outcome derives from beacon's verdicts under that declaration — imported runs
included. The derivation applies only where a verdict exists: DEFER and ERROR
remain the runner's statement about whether a query was produced at all,
never inferred from the absence of a passing verdict.

**Re-basing note:** Spider EX quoted before 2026-08-07 (53.4% on the local
slice) was the runner's own pre-convergence grading; from the derivation
change onward EX is beacon's (55.7% on the same run). The run config
(`graded_by`, `headline_metric`, `imported_sha256`) and `dataset_version`
distinguish the two eras; comparisons across them must re-base.

## got-facts

The gold's information is present, with extra columns or different formatting.

**Tolerated:** extra candidate columns (never a missing gold column); column
order; row order; a decimal rounding either way, under either rounding
convention (half-up and banker's — engines disagree on halves, and convention
is presentation); the same value spelled differently (Decimal/int/float, dates
as ISO strings, surrounding whitespace).

**Not tolerated:** a missing gold column; a different row count; a different
value; NULL vs 0 vs empty string; True vs 1.

**A number that arrived as text** is formatting, and it is erased where the
transport is read, never in the comparator: CSV gold is typed at ingest
(column-wise, the way the benchmark's own pandas read sees it), and runners
push natively-typed rows. `values_match` itself stays type-strict — coercing
`"4" == 4` globally would equate a genuinely textual code with a count.

## exact

The same result set: gold's columns exactly, **in gold's order**, no extras,
values equal with tolerance only for float noise. Row order per the item's
declared order sensitivity (curated `row_order_insensitive` outranks the
ORDER BY heuristic).

Column order is part of exact match, deliberately: BIRD's official evaluator
compares row tuples position-wise, so the strict number stays the one the
leaderboard publishes. Column order is one more thing got-facts tolerates and
exact does not. A pure column-order miss is diagnosed as `column_order`, not
as a value difference.

## Whole numbers

Compare exactly under BOTH readings. A count, a year, an id is right or wrong,
never within a tolerance band. (A whole number may still be a legal *rounding*
of a fractional gold under got-facts: 53 for 52.63 is presentation.)

## Numeric bounds

`5e-7` absolute / `1e-5` relative, either bound satisfying. Whole numbers
exact, as above. The rounding rule belongs to got-facts only.

## Duplicate rows

Undeclared, multiplicity means something: the same rows with different
multiplicity is not obviously the same answer, and beacon compares multisets.
BIRD's published EX compares `set(rows)` — duplicates collapse, in either
direction — so BIRD items declare `duplicate_rows_insignificant` in their
tolerance and the one grader honours it in both metrics. This matches a
published rule we do not endorse, adopted with eyes open so that exact on
BIRD is the number the leaderboard publishes (the gap was 24 cases in 2,899,
0.83 points understated).

## Numeric bounds vs BIRD's exactness

BIRD's own evaluator applies no numeric tolerance — it executes both sides in
one SQLite, so bit-identical floats are guaranteed. Beacon's runners cross
engines, where the same quantity arrives with different float noise, so the
bounds above apply everywhere. This is a deliberate, recorded divergence from
BIRD's letter; measured cost, 1 case in 2,899.

## String case

`"West"` and `"west"` are different values, deliberately: case distinguishes
real entities. This is a decision, not an accident of implementation.

## Multiple accepted golds

Gold is canonically a SET of accepted results; the candidate passes against
any of them, and the single-gold benchmark is the one-element case. A
per-accepted-result `condition_cols` restriction — the columns the benchmark's
own evaluator scores — loosens got-facts only, never exact.

## Truncated evidence

When the push is a preview (fewer visible rows than the true count), the true
counts still compare exactly and the visible rows are read by containment — a
weaker claim, recorded as `evidence_truncated` on the verdict. Such cases
COUNT as graded: excluding them would let a runner shrink the denominator by
pushing less, and the incentive must always point toward pushing full rows.
A reading that excludes them is quotable, but it is the footnote, not the
headline.

## Stored evidence

Dict-shaped rows carry their column order only while in flight: JSONB
canonicalizes object keys at rest, and column order is part of exact match.
So pushed and imported evidence is stamped with an ordered `columns` array at
arrival, the grader reads dict rows through it, and a regrade REFUSES
evidence that lacks one -- refusing beats mangling. BIRD verdicts graded
before the stamp (v1-v3) are the push-time record and are not re-derived at
later grader versions. That is chosen policy -- history as graded -- not
impossibility: the wire order each push carried survives in the v3 verdicts'
own recorded ``candidate_columns``, and re-execution of the recorded
``candidate_sql`` against the benchmark databases remains available. Someone
who needs the historical regrade can have it; nobody should mistake the
policy for a locked door. The version skew between suites is visible and
explained, not hidden.

## Imported runs

Beacon's verdict is THE claim. An imported run's results are graded here, by
the same grader as every push, and `Result.outcome` DERIVES from beacon's
verdict under the suite's `headline_metric`. The source system's own grading
survives only as provenance, in `output`. DEFER and ERROR are the exception
and remain the runner's statement: they say whether a query was produced at
all, and are never inferred from the absence of a passing verdict. A run's
config records what was read (`imported_sha256`) and whose outcomes rode
along (`source_runner`, `source_rev`), so which file and which grading
produced a number is never a memory.
