"""The importer's stamp, which nothing exercised while it was a dict literal."""

from __future__ import annotations

import pytest

from scripts.import_spider2_run import result_output


@pytest.mark.parametrize(
    ("engine_rows", "expected", "why"),
    [
        ([{"b": 1, "a": 2}], ["b", "a"], "wire order recorded, not sorted"),
        ([{"b": 1, "a": 2}, {"a": 3, "b": 4}], ["b", "a"], "key ORDER differs: stamp repairs it"),
        ([{"b": 1, "a": 2}, {"b": 3}], None, "key SET differs: no order to claim"),
        ([{"a": 1}, {"b": 2}], None, "same arity, different keys"),
        ([[1, 2]], None, "list rows keep their own order"),
        ([], None, "nothing to order"),
        (None, None, "no rows at all"),
    ],
)
def test_the_importer_stamps_through_the_shared_rule(
    engine_rows: object, expected: list[str] | None, why: str
) -> None:
    """Reading the call site is not enough to know the right key reaches it.

    Passing the wrong key to `column_order_for` returns None for every case,
    dropping the stamp from every imported result -- behind a green import, and
    invisible to a test suite that never imports this module. A regrade then
    refuses the multi-column ones and proceeds on the single-column ones,
    which is safe only because one column has no order to lose.
    """
    output = result_output({"engine_rows": engine_rows}, "correct")

    assert output["columns"] == expected, why
    assert output["rows"] == engine_rows, "the rows themselves pass through untouched"


def test_the_importer_carries_the_counts_and_the_engines_own_outcome() -> None:
    """The stamp is one field of a record the rest of the pipeline reads."""
    output = result_output(
        {
            "sql": "SELECT 1",
            "answer": "1",
            "db_id": "local001",
            "engine_rows": [{"n": 1}],
            "engine_row_count": 1,
            "gold_row_count": 2,
        },
        "wrong",
    )

    assert output["sql"] == "SELECT 1"
    # The drill-down renders `output.sql || output.answer`, so an ERROR case
    # carrying no SQL shows the answer instead of an empty box. Not a DEFER,
    # which short-circuits to "No SQL produced." before that fallback --
    # `deferred_correctly` maps to PASS and does reach it. Dropping this key
    # from the record fails nothing else.
    assert output["answer"] == "1"
    assert output["db_id"] == "local001"
    assert output["row_count"] == 1
    assert output["engine_row_count"] == 1
    assert output["gold_row_count"] == 2
    assert output["mnemiq_outcome"] == "wrong"


def test_the_count_is_the_engines_count_and_not_the_length_of_the_preview() -> None:
    """`rows` is a preview of the first N; `row_count` is how many there were.

    The grader reads `int(result.output.get("row_count") or len(candidate.rows))`,
    so sourcing the count from the preview instead would compare 3 against a
    gold of 5000 and emit a row_count mismatch on every truncated case. A
    fixture where the two are equal cannot tell the sources apart.
    """
    output = result_output(
        {"engine_rows": [{"n": 1}, {"n": 2}, {"n": 3}], "engine_row_count": 5000},
        "correct",
    )

    assert output["row_count"] == 5000
    assert output["engine_row_count"] == 5000
    assert len(output["rows"]) == 3
