"""The answer-authority grader: pushed rows against materialized gold."""

from __future__ import annotations

from typing import Any

import pytest
from beacon_graders.comparison import ResultSet, got_facts, got_facts_contained
from beacon_graders.graders.result_set_match import ResultSetMatchGrader, _project_gold
from beacon_graders.tolerance import Tolerance
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep


def _item(gold: dict[str, Any]) -> EvalItem:
    return EvalItem(
        item_id="i-1",
        suite="s",
        query={"question": "q?"},
        ground_truth=gold,
        metadata={},
    )


def _result(output: dict[str, Any]) -> ExecutionResult:
    return ExecutionResult(
        output=output,
        output_kind="sql",
        trace=ExecutionStep(uuid="root", name="root", level="workflow"),
        tokens_input=1,
        tokens_output=1,
        runtime_ms=1,
    )


def _gold(rows: list[list[Any]], **over: Any) -> dict[str, Any]:
    gold: dict[str, Any] = {
        "sql": "SELECT a FROM t",
        "columns": ["a"],
        "rows": rows,
        "row_count": len(rows),
    }
    gold.update(over)
    return gold


def _push(rows: list[Any], **over: Any) -> dict[str, Any]:
    output: dict[str, Any] = {"sql": "SELECT a FROM candidate", "rows": rows}
    output.update(over)
    return output


def _grade(gold: dict[str, Any], output: dict[str, Any]) -> tuple[Any, Any]:
    verdicts = ResultSetMatchGrader().grade(_item(gold), _result(output))
    exact = next(v for v in verdicts if v.metric != "got_facts")
    facts = next(v for v in verdicts if v.metric == "got_facts")
    return exact, facts


def test_not_applicable_without_pushed_rows() -> None:
    grader = ResultSetMatchGrader()
    assert not grader.applicable(_item(_gold([[1]])), _result({"sql": "SELECT 1"}))


def test_not_applicable_without_materialized_gold() -> None:
    grader = ResultSetMatchGrader()
    assert not grader.applicable(_item({"sql": "SELECT 1"}), _result(_push([[1]])))


def test_matching_rows_pass_exact() -> None:
    exact, facts = _grade(_gold([[1], [2]]), _push([[2], [1]]))

    assert exact.bool_value is True
    assert facts.bool_value is True
    assert exact.raw_output["evidence_complete"] is True


def test_wrong_values_fail_with_a_named_cell() -> None:
    exact, _ = _grade(_gold([[1]]), _push([[3]]))

    assert exact.bool_value is False
    assert exact.raw_output["mismatch"]["kind"] == "values"


def test_row_count_uses_true_counts_not_preview_length() -> None:
    """A 100-row preview of a 500-row answer against 400-row gold fails on count."""
    exact, facts = _grade(
        _gold([[i] for i in range(10)], row_count=400),
        _push([[i] for i in range(10)], row_count=500),
    )

    assert exact.bool_value is False
    assert facts.bool_value is False
    assert exact.raw_output["mismatch"]["kind"] == "row_count"


def test_truncated_evidence_grades_by_containment_and_says_so() -> None:
    """Counts agree, push holds a preview: visible rows must all be in gold."""
    gold_rows = [[i] for i in range(200)]
    exact, facts = _grade(
        _gold(gold_rows, row_count=200),
        _push([[i] for i in range(100)], row_count=200),
    )

    assert exact.bool_value is True
    assert facts.bool_value is True
    assert exact.raw_output["evidence_truncated"] is True


def test_truncated_evidence_with_a_foreign_row_fails() -> None:
    gold_rows = [[i] for i in range(200)]
    exact, _ = _grade(
        _gold(gold_rows, row_count=200),
        _push([[999999]], row_count=200),
    )

    assert exact.bool_value is False


def test_extra_columns_fail_exact_but_pass_got_facts() -> None:
    exact, facts = _grade(
        _gold([["04"]]),
        _push([["04", 126047744.0]], columns=["month", "total"]),
    )

    assert exact.bool_value is False
    assert exact.raw_output["mismatch"]["kind"] == "column_arity"
    assert facts.bool_value is True


def test_mnemiq_report_shape_rows_as_dicts() -> None:
    """mnemiq previews are lists of {column: value} dicts; both sides parse."""
    exact, _ = _grade(
        _gold([[1, "x"]], columns=["id", "name"]),
        _push([{"id": 1, "name": "x"}]),
    )

    assert exact.bool_value is True
    assert exact.raw_output["candidate_columns"] == ["id", "name"]


def test_cross_engine_value_spellings_canonicalize() -> None:
    """A date object on one side and its ISO string on the other still match."""
    from datetime import date

    exact, _ = _grade(
        _gold([["2013-06-01", 5.0]], columns=["d", "n"]),
        _push([[date(2013, 6, 1), 5]]),
    )

    assert exact.bool_value is True


def test_null_matches_only_null() -> None:
    exact_null, _ = _grade(_gold([[None]]), _push([[None]]))
    exact_zero, _ = _grade(_gold([[None]]), _push([[0]]))
    exact_empty, _ = _grade(_gold([[None]]), _push([[""]]))

    assert exact_null.bool_value is True
    assert exact_zero.bool_value is False
    assert exact_empty.bool_value is False


def test_order_matters_when_gold_orders() -> None:
    gold = _gold([[1], [2]], sql="SELECT a FROM t ORDER BY a")
    exact_wrong_order, _ = _grade(gold, _push([[2], [1]]))
    exact_right_order, _ = _grade(gold, _push([[1], [2]]))

    assert exact_wrong_order.bool_value is False
    assert exact_right_order.bool_value is True


def test_engine_and_portability_ride_into_the_evidence() -> None:
    exact, _ = _grade(
        _gold([[1]]),
        _push([[1]], engine="duckdb", portable_to_gold_engine=False),
    )

    assert exact.raw_output["engine"] == "duckdb"
    assert exact.raw_output["portable_to_gold_engine"] is False


def test_a_rounded_presentation_passes_got_facts_but_not_exact() -> None:
    """66.62 where gold computes 66.6230: right quantity, different spelling."""
    exact, facts = _grade(_gold([[66.6230080768]]), _push([[66.62]]))

    assert exact.bool_value is False
    assert facts.bool_value is True


def test_gold_that_rounds_forgives_the_unrounded_candidate_in_got_facts() -> None:
    """Gold applies ROUND(x, 2); the candidate returned the unrounded value."""
    exact, facts = _grade(_gold([[8.77]]), _push([[8.773399659641314]]))

    assert exact.bool_value is False
    assert facts.bool_value is True


def test_a_genuinely_different_number_fails_both_metrics() -> None:
    """52.128 is not a rounding of 52.632 at any precision; ~1%% apart is wrong."""
    exact, facts = _grade(_gold([[52.63157894736842]]), _push([[52.12765884399414]]))

    assert exact.bool_value is False
    assert facts.bool_value is False


def test_engine_float_noise_now_passes_exact() -> None:
    """0.8 spelled by another engine's float path is the same number."""
    exact, _ = _grade(_gold([[0.7999999999999972]]), _push([[0.8000030517578125]]))

    assert exact.bool_value is True


def test_whole_numbers_still_compare_exactly() -> None:
    exact, facts = _grade(_gold([[132236.0]]), _push([[132235.0]]))

    assert exact.bool_value is False
    assert facts.bool_value is False


def test_zero_place_rounding_does_not_collapse_small_quantities() -> None:
    """0.0 for 0.196 did not get the fact; 53 for 52.63 is presentation."""
    _, facts_small = _grade(_gold([[0.19569471624266144]]), _push([[0.0]]))
    _, facts_large = _grade(_gold([[52.63]]), _push([[53.0]]))

    assert facts_small.bool_value is False
    assert facts_large.bool_value is True


def test_tie_order_variance_fails_exact_but_passes_got_facts() -> None:
    """Same rows, order differing at tied sort keys: the facts were got."""
    gold = _gold([[1], [2], [3]], sql="SELECT a FROM t ORDER BY score")
    exact, facts = _grade(gold, _push([[1], [3], [2]]))

    assert exact.bool_value is False
    assert facts.bool_value is True


# ---- multi-gold: a set of accepted results, any of which passes (v4) ----


def _accepted(*tables: dict[str, Any], **over: Any) -> dict[str, Any]:
    gold: dict[str, Any] = {"accepted_results": list(tables), "sql": ""}
    gold.update(over)
    return gold


def _table(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"columns": columns, "rows": rows}


def test_applicable_with_accepted_results_and_no_top_level_rows() -> None:
    grader = ResultSetMatchGrader()
    gold = _accepted(_table(["a"], [[1]]))

    assert grader.applicable(_item(gold), _result(_push([[1]])))


def test_any_accepted_result_passes_and_names_which() -> None:
    # Spider 2.0-lite publishes several acceptable answers; matching any one
    # of them is a pass, and the drill-down should say which one it was.
    gold = _accepted(_table(["a"], [[1], [2]]), _table(["total"], [[3], [4]]))
    exact, facts = _grade(gold, _push([[3], [4]]))

    assert exact.bool_value is True
    assert facts.bool_value is True
    assert exact.raw_output["matched_accepted_index"] == 1
    assert exact.raw_output["accepted_result_count"] == 2


def test_no_accepted_result_matching_fails_with_the_first_golds_diagnosis() -> None:
    gold = _accepted(_table(["a"], [[1]]), _table(["a"], [[2]]))
    exact, facts = _grade(gold, _push([[9]]))

    assert exact.bool_value is False
    assert facts.bool_value is False
    assert exact.raw_output["mismatch"]["kind"] == "values"


def test_row_count_gates_each_accepted_result_separately() -> None:
    # One accepted answer has two rows, the other three; a two-row candidate
    # is compared against the two-row gold, not refused outright.
    gold = _accepted(_table(["a"], [[1], [2]]), _table(["a"], [[1], [2], [3]]))
    exact, _ = _grade(gold, _push([[1], [2]]))

    assert exact.bool_value is True


def test_condition_cols_loosen_got_facts_but_never_exact() -> None:
    # The benchmark scores only column 0 of this gold (condition_cols). The
    # strict reading still demands the full table, so exact stays comparable
    # across benchmarks; the tolerant reading honours the curation.
    gold = _accepted(
        _table(["id", "note"], [[1, "x"], [2, "y"]]),
        condition_cols=[[0]],
    )
    exact, facts = _grade(gold, _push([[1, "different"], [2, "other"]]))

    assert exact.bool_value is False
    assert facts.bool_value is True
    assert exact.raw_output["condition_cols"] == [[0]]


def test_condition_cols_do_not_rescue_wrong_scored_columns() -> None:
    gold = _accepted(
        _table(["id", "note"], [[1, "x"], [2, "y"]]),
        condition_cols=[[0]],
    )
    exact, facts = _grade(gold, _push([[7, "x"], [8, "y"]]))

    assert exact.bool_value is False
    assert facts.bool_value is False


def test_half_up_rounding_is_a_rounding_too() -> None:
    """38.13 for 38.125 is half-up; Python's round() is banker's. Which
    convention an engine rounds with is presentation, not a different answer.
    (Spider local023 is exactly this pair.)"""
    _, facts_half_up = _grade(_gold([[38.125]]), _push([[38.13]]))
    _, facts_half_even = _grade(_gold([[38.125]]), _push([[38.12]]))
    exact, _ = _grade(_gold([[38.125]]), _push([[38.13]]))

    assert facts_half_up.bool_value is True
    assert facts_half_even.bool_value is True
    assert exact.bool_value is False


def test_a_near_miss_is_not_a_rounding() -> None:
    """6.47 for 6.48 rounds from nothing: a genuinely different average.
    (Spider local152.)"""
    exact, facts = _grade(_gold([[6.48]]), _push([[6.47]]))

    assert exact.bool_value is False
    assert facts.bool_value is False


def test_column_order_fails_exact_names_the_reason_and_the_closest_gold() -> None:
    """Point H: exact is position-wise (BIRD's published metric is). A candidate
    matching an accepted gold with SELECT order swapped fails exact, passes
    got-facts, and the mismatch is diagnosed against the gold it nearly
    matched -- naming column order, not a value difference. (Spider local067.)"""
    gold = _accepted(
        _table(["tier", "lowest", "highest"], [[1, -27.94, -10.03]]),
        _table(["tier", "lowest", "highest"], [[1, 588.36, 785.15]]),
    )
    exact, facts = _grade(gold, _push([[1, 785.15, 588.36]]))

    assert exact.bool_value is False
    assert facts.bool_value is True
    assert exact.raw_output["mismatch"]["kind"] == "column_order"
    assert exact.raw_output["gold_sample"] == [[1, 588.36, 785.15]]


# ---- duplicate_rows_insignificant: BIRD's published set() rule (v5) ----


def _flagged(gold: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return gold, {"tolerance": {"duplicate_rows_insignificant": True}}


def _grade_with_meta(
    gold: dict[str, Any], metadata: dict[str, Any], output: dict[str, Any]
) -> tuple[Any, Any]:
    item = EvalItem(
        item_id="i-1", suite="s", query={"question": "q?"}, ground_truth=gold, metadata=metadata
    )
    verdicts = ResultSetMatchGrader().grade(item, _result(output))
    exact = next(v for v in verdicts if v.metric != "got_facts")
    facts = next(v for v in verdicts if v.metric == "got_facts")
    return exact, facts


def test_duplicates_collapse_when_the_item_declares_them_insignificant() -> None:
    """bird-1411's shape: gold repeats rows (12 -> 7 distinct), the candidate
    returns the 7 distinct. BIRD's published EX compares set(); an item that
    declares duplicate_rows_insignificant grades the way its leaderboard does."""
    gold, meta = _flagged(_gold([[1], [1], [2], [2], [3]], row_count=5))
    exact, facts = _grade_with_meta(gold, meta, _push([[1], [2], [3]]))

    assert exact.bool_value is True
    assert facts.bool_value is True
    assert exact.raw_output["duplicate_rows_insignificant"] is True


def test_duplicates_collapse_in_the_candidate_direction_too() -> None:
    """bird-1435's shape: candidate repeats a row (3 -> 2 distinct)."""
    gold, meta = _flagged(_gold([[1], [2]], row_count=2))
    exact, _ = _grade_with_meta(gold, meta, _push([[1], [2], [2]]))

    assert exact.bool_value is True


def test_without_the_flag_multiplicity_still_means_something() -> None:
    """Undeclared, beacon's multiset claim stands: the same rows with
    different multiplicity is not obviously the same answer."""
    exact, facts = _grade(_gold([[1], [1], [2], [2], [3]], row_count=5), _push([[1], [2], [3]]))

    assert exact.bool_value is False
    assert facts.bool_value is False


# ---- stored evidence: JSONB scrambles dict keys; columns arrays do not ----


def test_stored_dict_rows_grade_by_the_stamped_column_order() -> None:
    """JSONB canonicalizes object keys at rest, so dict rows read back from
    storage have lost their wire order. An ordered columns array restores it,
    and must win over insertion order. (Found when a regrade over scrambled
    rows flipped 699 outcomes before being reverted.)"""
    gold = _gold([["Ann", "Smith"]], columns=["first", "last"])
    scrambled = _push([{"last": "Smith", "first": "Ann"}], columns=["first", "last"], row_count=1)
    exact, _ = _grade(gold, scrambled)

    assert exact.bool_value is True


def test_dict_rows_without_a_columns_array_trust_insertion_order() -> None:
    """An in-flight dict (never stored) still carries the wire order."""
    gold = _gold([["Ann", "Smith"]], columns=["first", "last"])
    exact, _ = _grade(gold, _push([{"first": "Ann", "last": "Smith"}], row_count=1))

    assert exact.bool_value is True


def test_a_preview_with_insignificant_duplicates_grades_by_containment() -> None:
    """The declared raw count and the distinct gold count are not comparable
    when the push is a preview -- the count gate stands down and the visible
    rows decide by containment."""
    gold, meta = _flagged(_gold([[1], [1], [2]], row_count=3))
    exact, facts = _grade_with_meta(gold, meta, _push([[1], [2]], row_count=3))

    assert exact.bool_value is True
    assert facts.bool_value is True


def test_a_gold_projected_to_zero_columns_is_not_got_facts() -> None:
    """A claim about no columns is not a claim, and it used to pass everything.

    `_project_gold` drops out-of-range indices silently, so an annotation whose
    indices ALL fall outside the gold's arity yields a zero-column gold. The
    tolerant readings then compared empty tuples row-for-row and returned True
    for any candidate with the right row count -- got-facts reduced to "did you
    return the right NUMBER of rows", on content it never looked at. Latent on
    today's corpus (measured 2026-09-03: 0 of 132 restricted variants carry an
    out-of-range index) and fail-open, which is the combination worth a guard.
    """
    gold = ResultSet(columns=["MONTH", "TOTAL"], rows=[("2020-01", 356618), ("2020-02", 409593)])
    candidate = ResultSet(columns=["a", "b"], rows=[("wildly", 1.0), ("wrong", 2.0)])

    projected = _project_gold(gold, (7, 9))

    assert projected.rows == [(), ()]
    assert got_facts(candidate, projected, Tolerance()) is False
    assert got_facts_contained(candidate, projected, Tolerance()) is False


def test_a_partially_out_of_range_restriction_keeps_the_valid_columns() -> None:
    """The guard must not punish a usable annotation: only ALL-invalid is vacuous."""
    gold = ResultSet(columns=["MONTH", "TOTAL"], rows=[("2020-01", 356618), ("2020-02", 409593)])
    exact = ResultSet(columns=["m", "t"], rows=[("2020-01", 356618), ("2020-02", 409593)])

    projected = _project_gold(gold, (0, 9))

    assert projected.rows == [("2020-01",), ("2020-02",)]
    assert got_facts(exact, projected, Tolerance()) is True


def test_a_subset_scored_got_facts_verdict_names_the_columns_it_compared() -> None:
    """local300's shape: got-facts true on a candidate whose value is 6x off.

    `condition_cols` restricts the tolerant reading to the benchmark's scored
    columns, so on 45 of spider2's 135 items this metric asks about a strict
    subset of gold. Reported as a bare boolean it reads as "the facts are
    there" either way. The scope WAS recorded -- on the exact-match verdict,
    whose reading it does not affect -- so a reader of the verdict that
    actually carries the metric had to fetch the gold to learn what was
    compared.
    """
    item = _item(
        {
            "condition_cols": [[0]],
            "accepted_results": [
                {"columns": ["MONTH", "TOTAL"], "rows": [["2020-01", 356618], ["2020-02", 409593]]}
            ],
        }
    )
    result = _result({"rows": [["2020-01", 2120567.0], ["2020-02", 5636091.0]]})

    verdicts = ResultSetMatchGrader().grade(item, result)
    facts = next(v for v in verdicts if v.metric == "got_facts")

    assert facts.bool_value is True
    assert "MONTH" in facts.justification
    assert "not a claim about them" in facts.justification
    assert facts.raw_output is not None
    assert facts.raw_output["scored_columns"] == ["MONTH"]
    assert facts.raw_output["condition_cols"] == [0]


def test_a_restriction_naming_EVERY_column_does_not_claim_a_scope() -> None:
    """The no-op restriction: present, but covering the whole table.

    Four of spider2's 135 items are like this -- `condition_cols` naming every
    column, so nothing is actually narrowed. Saying "compared on the
    benchmark-scored columns only: MONTH, TOTAL" there would assert a
    restriction the item does not impose, and the reader would discount a
    verdict that is in fact a full-table claim.

    This is the case the sibling no-condition_cols test does NOT reach: it
    returns early on the empty check and never exercises the arity comparison,
    so deleting `if len(kept) >= arity` left every other test green.
    """
    item = _item(
        {
            "condition_cols": [[0, 1]],
            "accepted_results": [{"columns": ["MONTH", "TOTAL"], "rows": [["2020-01", 356618]]}],
        }
    )
    result = _result({"rows": [["2020-01", 356618]]})

    verdicts = ResultSetMatchGrader().grade(item, result)
    facts = next(v for v in verdicts if v.metric == "got_facts")

    assert facts.bool_value is True
    assert "benchmark-scored columns only" not in facts.justification
    assert facts.raw_output is not None
    assert "scored_columns" not in facts.raw_output


def test_an_unrestricted_got_facts_verdict_does_not_claim_a_scope() -> None:
    """No condition_cols at all means the whole table was compared."""
    item = _item(
        {"accepted_results": [{"columns": ["MONTH", "TOTAL"], "rows": [["2020-01", 356618]]}]}
    )
    result = _result({"rows": [["2020-01", 356618]]})

    verdicts = ResultSetMatchGrader().grade(item, result)
    facts = next(v for v in verdicts if v.metric == "got_facts")

    assert facts.bool_value is True
    assert "benchmark-scored columns only" not in facts.justification
    assert facts.raw_output is not None
    assert "scored_columns" not in facts.raw_output


def test_an_EXACT_match_on_a_restricted_item_claims_no_narrowed_scope() -> None:
    """The mirror of the local300 over-claim, and it shipped in the same change.

    `facts_v = passed_v or got_facts(...)`, so got-facts is true whenever the
    full table matched -- and `facts_index` is then set on a variant that
    happens to carry a restriction. Narrating "compared on the benchmark-scored
    columns only: MONTH. Other gold columns were not compared" for a candidate
    equal to gold on EVERY column describes the wrong reading, and 45 of the
    restricted items would have had every exact-match pass narrated that way.
    """
    item = _item(
        {
            "condition_cols": [[0]],
            "accepted_results": [{"columns": ["MONTH", "TOTAL"], "rows": [["2020-01", 356618]]}],
        }
    )
    result = _result({"rows": [["2020-01", 356618]]})

    verdicts = ResultSetMatchGrader().grade(item, result)
    exact = next(v for v in verdicts if v.metric != "got_facts")
    facts = next(v for v in verdicts if v.metric == "got_facts")

    assert exact.bool_value is True
    assert facts.bool_value is True
    assert "benchmark-scored columns only" not in facts.justification
    assert facts.raw_output is not None
    assert "scored_columns" not in facts.raw_output


def test_a_REPEATED_condition_col_still_discloses_its_narrowed_scope() -> None:
    """`[0, 0]` on a 2-column gold scores ONE distinct column, not two.

    Coverage is a set question. Counting the raw list made `len(kept) >= arity`
    true, so the disclosure returned None and said nothing about a reading that
    never looked at TOTAL -- failing open on precisely the input it exists to
    describe.

    `_project_gold` dedupes too, and that is a reading decision rather than
    tidiness -- see its own comment and the sibling test below.
    """
    item = _item(
        {
            "condition_cols": [[0, 0]],
            "accepted_results": [
                {"columns": ["MONTH", "TOTAL"], "rows": [["2020-01", 356618], ["2020-02", 409593]]}
            ],
        }
    )
    result = _result({"rows": [["2020-01", "2020-01"], ["2020-02", "2020-02"]]})

    verdicts = ResultSetMatchGrader().grade(item, result)
    facts = next(v for v in verdicts if v.metric == "got_facts")

    assert facts.bool_value is True
    assert facts.raw_output is not None
    assert facts.raw_output["scored_columns"] == ["MONTH"]
    assert "MONTH" in facts.justification
    assert "TOTAL" not in facts.justification


def test_a_REPEATED_condition_col_does_not_deny_a_candidate_that_carries_it() -> None:
    """The false negative the duplicate projection produced.

    `_project_gold` used to duplicate a repeated index, so `condition_cols
    [0, 0]` on a 2-column gold projected to arity 2 (MONTH, MONTH). A candidate
    returning that one scored column ONCE, with the right values, then tripped
    `gold_arity > candidate_arity` and got "Gold's data is not present in the
    candidate, in any column projection" -- a false statement about a candidate
    that carried exactly the scored data. It also graded stricter than the
    benchmark, whose match loop consumes nothing, so one prediction column
    satisfies both copies. Deduping agrees with upstream and removes both.
    """
    item = _item(
        {
            "condition_cols": [[0, 0]],
            "accepted_results": [
                {"columns": ["MONTH", "TOTAL"], "rows": [["2020-01", 356618], ["2020-02", 409593]]}
            ],
        }
    )
    result = _result({"rows": [["2020-01"], ["2020-02"]]})

    facts = next(v for v in ResultSetMatchGrader().grade(item, result) if v.metric == "got_facts")

    assert facts.bool_value is True
    assert facts.raw_output is not None
    assert facts.raw_output["scored_columns"] == ["MONTH"]


def test_an_exact_match_on_a_LATER_accepted_result_claims_no_narrowed_scope() -> None:
    """The cross-variant fail-open, which is the same bug one variant over.

    The gate was recorded per variant, at the moment `facts_index` was first
    assigned. So when an EARLIER accepted result facts-matched by projection
    and a LATER one matched the full table exactly, the verdict still narrated
    the narrowed scope. Restricted spider2 items routinely publish several
    accepted results differing only in column order, so this shape is ordinary.
    The gate now reads the aggregate `passed` after the loop.
    """
    rows = [["2020-01", 356618], ["2020-02", 409593]]
    flipped = [[r[1], r[0]] for r in rows]
    item = _item(
        {
            "condition_cols": [[0], [0]],
            "accepted_results": [
                {"columns": ["MONTH", "TOTAL"], "rows": rows},
                {"columns": ["TOTAL", "MONTH"], "rows": flipped},
            ],
        }
    )
    result = _result({"rows": flipped})

    verdicts = ResultSetMatchGrader().grade(item, result)
    exact = next(v for v in verdicts if v.metric != "got_facts")
    facts = next(v for v in verdicts if v.metric == "got_facts")

    assert exact.bool_value is True
    assert facts.bool_value is True
    assert "benchmark-scored columns only" not in facts.justification
    assert facts.raw_output is not None
    assert "scored_columns" not in facts.raw_output

    # BOTH rows must name the accepted result that carried the verdict. The
    # got-facts row reported the first variant to facts-match by PROJECTION
    # (0), while the exact row reported the one that matched (1) -- two rows
    # for one result attributing it to different golds, and suppressing
    # `scored_columns` had removed the only marker that distinguished the two
    # readings, so the mislabel was all a reader saw.
    assert exact.raw_output is not None
    assert exact.raw_output["matched_accepted_index"] == 1
    assert facts.raw_output["matched_accepted_index"] == 1


@pytest.mark.parametrize(
    ("gold_extra", "candidate_total", "expected_scope"),
    [
        # Exact must FAIL for the projection to be what carried got-facts:
        # a candidate equal to gold on every column reports "full", correctly,
        # because no restriction was applied to reach that verdict.
        ({"condition_cols": [[0]]}, 999, "subset"),
        ({"condition_cols": [[0, 1]]}, 356618, "full"),
        ({}, 356618, "full"),
    ],
    ids=["restricted", "restriction-covers-everything", "no-restriction"],
)
def test_the_got_facts_verdict_ALWAYS_declares_its_scope(
    gold_extra: dict[str, Any], candidate_total: int, expected_scope: str
) -> None:
    """A positive marker, because absence has to mean exactly one thing.

    The aggregate counts subset-scored readings apart from full-table ones
    (B72). If "full" were inferred from a MISSING key, then a verdict written
    before the scope existed would be indistinguishable from one that compared
    every column -- and every historical row would count as full-table-scored.
    That is an absence rendered as a measurement, the family B65 and B67 belong
    to, and it is the reason `scope` is written on every got-facts verdict
    rather than only on the restricted ones.

    So NULL means one thing: this verdict predates the disclosure. Deleting the
    `scope = "full"` line left every other test in this file green.
    """
    gold: dict[str, Any] = {
        "accepted_results": [{"columns": ["MONTH", "TOTAL"], "rows": [["2020-01", 356618]]}]
    }
    gold.update(gold_extra)
    result = _result({"rows": [["2020-01", candidate_total]]})

    facts = next(
        v for v in ResultSetMatchGrader().grade(_item(gold), result) if v.metric == "got_facts"
    )

    assert facts.raw_output is not None
    assert facts.raw_output["scope"] == expected_scope


@pytest.mark.parametrize(
    ("candidate_rows", "expected_bool"),
    [
        ([["2020-01", 999]], True),
        ([["2020-02", 999]], False),
    ],
    ids=["passes-the-scored-column", "fails-the-scored-column"],
)
def test_the_scope_describes_the_QUESTION_not_the_answer(
    candidate_rows: list[list[Any]], expected_bool: bool
) -> None:
    """A restricted item is subset-scoped whether the model got it right or not.

    An earlier version read the scope off the variant that CARRIED a true
    verdict, so a restricted item that FAILED got-facts had no carrier and was
    stamped "full". `n_got_facts_subset_scored` could then only ever count the
    restricted items a model PASSED -- a composition that moves with the pass
    rate it exists to qualify, which is worse than publishing no breakdown at
    all. Measured before the fix: `condition_cols [[0]]` with a candidate wrong
    on the scored column gave `bool_value=False, scope="full"`.

    The three cases of `test_the_got_facts_verdict_ALWAYS_declares_its_scope`
    all reach a non-None carrier, so the entire failing-restricted region was
    unread. That is what this covers.
    """
    item = _item(
        {
            "condition_cols": [[0]],
            "accepted_results": [{"columns": ["MONTH", "TOTAL"], "rows": [["2020-01", 356618]]}],
        }
    )

    facts = next(
        v
        for v in ResultSetMatchGrader().grade(item, _result({"rows": candidate_rows}))
        if v.metric == "got_facts"
    )

    assert facts.bool_value is expected_bool
    assert facts.raw_output is not None
    assert facts.raw_output["scope"] == "subset"
