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
information about how the benchmark grades, not a bug.

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

The same result set: gold's columns exactly, no extras, values equal with
tolerance only for float noise. Row order per the item's declared order
sensitivity (curated `row_order_insensitive` outranks the ORDER BY heuristic).

## Whole numbers

Compare exactly under BOTH readings. A count, a year, an id is right or wrong,
never within a tolerance band. (A whole number may still be a legal *rounding*
of a fractional gold under got-facts: 53 for 52.63 is presentation.)

## Numeric bounds

`5e-7` absolute / `1e-5` relative, either bound satisfying. Whole numbers
exact, as above. The rounding rule belongs to got-facts only.

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

## Imported runs

Beacon's verdict is THE claim. An imported run keeps the source system's
grading as provenance (`Result.outcome` under the benchmark's headline rule,
the raw outcome in `output`), and its results are graded here, by the same
grader as every push. A run's config records what was read
(`imported_sha256`), so which file produced a number is never a memory.
