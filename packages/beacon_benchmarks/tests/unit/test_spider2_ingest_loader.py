from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from beacon_benchmarks.spider2_lite.ingest_items import load_spider2_tasks

if TYPE_CHECKING:
    from pathlib import Path


def _write_eval_annotations(repo: Path, records: list[dict[str, object]]) -> None:
    (repo / "evaluation_suite" / "gold" / "spider2lite_eval.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8"
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "spider2-lite"
    (repo / "resource" / "documents").mkdir(parents=True)
    (repo / "evaluation_suite" / "gold" / "sql").mkdir(parents=True)
    (repo / "evaluation_suite" / "gold" / "exec_result").mkdir(parents=True)

    records = [
        {
            "instance_id": "local001",
            "db": "chinook",
            "question": "How many tracks?",
            "external_knowledge": "tracks.md",
        },
        {
            "instance_id": "local002",
            "db": "northwind",
            "question": "Total orders?",
            "external_knowledge": None,
        },
        # Cloud instances need credentials and adapters beacon does not have here.
        {
            "instance_id": "bq001",
            "db": "cloudy",
            "question": "Not local",
            "external_knowledge": None,
        },
    ]
    (repo / "spider2-lite.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8"
    )
    (repo / "resource" / "documents" / "tracks.md").write_text(
        "A track is one song.", encoding="utf-8"
    )
    (repo / "evaluation_suite" / "gold" / "sql" / "local001.sql").write_text(
        "SELECT count(*) FROM track", encoding="utf-8"
    )
    exec_dir = repo / "evaluation_suite" / "gold" / "exec_result"
    # Two columns, so a broadcast `[1]` is a VALID index on both accepted
    # results. With 1-column tables the broadcast test was pinning an
    # out-of-range annotation rather than broadcasting.
    (exec_dir / "local001_a.csv").write_text("n,label\n3527,a\n", encoding="utf-8")
    (exec_dir / "local001_b.csv").write_text("total,label\n3527,a\n", encoding="utf-8")
    (exec_dir / "local002.csv").write_text("orders\n830\n", encoding="utf-8")
    _write_eval_annotations(
        repo,
        [
            {"instance_id": "local001", "condition_cols": [[0], [0]], "ignore_order": True},
            {"instance_id": "local002", "condition_cols": [], "ignore_order": False},
        ],
    )
    return repo


def test_only_local_instances_are_loaded(tmp_path: Path) -> None:
    tasks = load_spider2_tasks(_repo(tmp_path))

    assert [t.instance_id for t in tasks] == ["local001", "local002"]


def test_every_accepted_result_is_carried_not_just_the_first(tmp_path: Path) -> None:
    # Nearly every Spider 2.0-lite case publishes several acceptable answers; keeping
    # one would assert a single right result the benchmark does not claim exists.
    tasks = load_spider2_tasks(_repo(tmp_path))

    first = next(t for t in tasks if t.instance_id == "local001")
    assert len(first.accepted_results) == 2
    assert first.accepted_results[0] == {"columns": ["n", "label"], "rows": [[3527, "a"]]}
    assert first.accepted_results[1] == {"columns": ["total", "label"], "rows": [[3527, "a"]]}


def test_the_reference_document_is_loaded_as_evidence(tmp_path: Path) -> None:
    tasks = load_spider2_tasks(_repo(tmp_path))

    assert next(t for t in tasks if t.instance_id == "local001").external_knowledge == (
        "A track is one song."
    )
    assert next(t for t in tasks if t.instance_id == "local002").external_knowledge == ""


def test_gold_sql_is_optional_documentation(tmp_path: Path) -> None:
    tasks = load_spider2_tasks(_repo(tmp_path))

    assert next(t for t in tasks if t.instance_id == "local001").gold_sql == (
        "SELECT count(*) FROM track"
    )
    # Most cases publish result CSVs and no query at all.
    assert next(t for t in tasks if t.instance_id == "local002").gold_sql == ""


def test_selected_dbs_and_limit_filter(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    assert [t.instance_id for t in load_spider2_tasks(repo, selected_dbs=("northwind",))] == [
        "local002"
    ]
    assert len(load_spider2_tasks(repo, limit=1)) == 1


def test_csv_gold_is_typed_column_wise_like_the_benchmarks_pandas_read(tmp_path: Path) -> None:
    # Grading compares typed values: leaving gold stringly would fail 42
    # against "42" on every numeric column. One stray annotation demotes the
    # whole column to text, exactly as pandas reads these CSVs.
    repo = _repo(tmp_path)
    exec_dir = repo / "evaluation_suite" / "gold" / "exec_result"
    (exec_dir / "local002.csv").write_text(
        "orders,rate,region,note\n830,0.5,east,ok\n,1.25,west,2nd\n", encoding="utf-8"
    )

    task = next(t for t in load_spider2_tasks(repo) if t.instance_id == "local002")

    assert task.accepted_results[0]["rows"] == [
        [830, 0.5, "east", "ok"],
        [None, 1.25, "west", "2nd"],
    ]


def test_grading_annotations_ride_along_as_item_data(tmp_path: Path) -> None:
    # ignore_order and condition_cols come from the benchmark's own eval file;
    # dropping them grades stricter than the benchmark's evaluator does.
    tasks = load_spider2_tasks(_repo(tmp_path))

    first = next(t for t in tasks if t.instance_id == "local001")
    assert first.ignore_order is True
    assert first.condition_cols == [[0], [0]]
    second = next(t for t in tasks if t.instance_id == "local002")
    assert second.ignore_order is False
    assert second.condition_cols == []


def test_flat_condition_cols_broadcast_to_every_accepted_result(tmp_path: Path) -> None:
    # The evaluator's other accepted shape: a flat index list means "these
    # columns, for each accepted result" (see compare_multi_pandas_table).
    repo = _repo(tmp_path)
    _write_eval_annotations(
        repo, [{"instance_id": "local001", "condition_cols": [1], "ignore_order": True}]
    )

    tasks = load_spider2_tasks(repo)

    first = next(t for t in tasks if t.instance_id == "local001")
    assert first.condition_cols == [[1], [1]]
    # An instance absent from the annotations file carries no curated opinion.
    second = next(t for t in tasks if t.instance_id == "local002")
    assert second.ignore_order is None
    assert second.condition_cols == []


def test_positional_condition_cols_count_mismatch_refuses(tmp_path: Path) -> None:
    # One entry per accepted result, or nothing: restricting the wrong table's
    # columns would grade wrongly in silence.
    repo = _repo(tmp_path)
    _write_eval_annotations(
        repo, [{"instance_id": "local001", "condition_cols": [[0]], "ignore_order": True}]
    )

    with pytest.raises(ValueError, match="local001"):
        load_spider2_tasks(repo)


def test_missing_annotations_file_is_an_error(tmp_path: Path) -> None:
    # The benchmark always ships it; loading without it silently drops curated
    # grading semantics, which is how the ~3-point undercount happened.
    repo = _repo(tmp_path)
    (repo / "evaluation_suite" / "gold" / "spider2lite_eval.jsonl").unlink()

    with pytest.raises(FileNotFoundError, match="eval annotations"):
        load_spider2_tasks(repo)


def test_a_case_with_no_published_result_still_loads(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    records = json.loads(
        json.dumps(
            {
                "instance_id": "local003",
                "db": "chinook",
                "question": "Q",
                "external_knowledge": None,
            }
        )
    )
    with (repo / "spider2-lite.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n" + json.dumps(records))

    task = next(t for t in load_spider2_tasks(repo) if t.instance_id == "local003")

    # Loading must not hide it -- the runner reports it as an ERROR rather than
    # silently passing a case nothing can grade.
    assert task.accepted_results == []


def test_positional_condition_cols_out_of_range_refuses(tmp_path: Path) -> None:
    """An index naming a column its own accepted result does not have.

    The shape checks validate the ENVELOPE -- entry count, element types -- and
    never the numbers inside it, so this used to import cleanly and then grade
    on fewer columns than the annotation named: `_project_gold` keeps only the
    in-range indices. When NONE survive it projects gold to zero columns, and
    the tolerant readings compared empty tuples row-for-row and passed any
    candidate with the right row count. The grader now refuses a zero-column
    gold, but a guard there turns a bad annotation into a silent FAIL -- wrong
    place, wrong answer. The annotation is what is wrong; say so at import.

    Both forms are checked. An earlier version exempted broadcast, citing the
    sibling broadcast fixture as having tables of differing arity; it did not --
    both were 1-column, so that fixture pinned an out-of-range annotation. The
    fixture now uses 2-column tables so it pins BROADCASTING.

    Upstream raises on a POSITIVE overshoot in either annotation form, since
    the flat list is broadcast before `iloc` sees it. It does NOT raise on a
    negative index -- see the sibling negative-index test, and
    `_check_condition_col_range` for why that case is refused anyway.
    """
    repo = _repo(tmp_path)
    _write_eval_annotations(
        repo,
        [{"instance_id": "local001", "condition_cols": [[0], [4]], "ignore_order": True}],
    )

    with pytest.raises(ValueError, match="names column"):
        load_spider2_tasks(repo)


def test_a_NEGATIVE_condition_col_refuses(tmp_path: Path) -> None:
    """The other bound, and the one an upper-bound-only test leaves open.

    `_project_gold` filters on `0 <= i < arity`, so a negative index is dropped
    exactly as silently as an oversized one -- and with a single-entry
    annotation that drop projects gold to zero columns. Checking only the upper
    bound left `0 <= ` deletable with the whole suite green.

    It is the worse of the two bounds, not the milder one: upstream's
    `gold.iloc[:, [-1]]` does NOT raise, it selects the last column, so a
    negative index makes beacon and the benchmark score DIFFERENT columns on
    the same annotation with nothing reporting it. An IndexError would at least
    be loud on one side.
    """
    repo = _repo(tmp_path)
    _write_eval_annotations(
        repo,
        [{"instance_id": "local001", "condition_cols": [[0], [-1]], "ignore_order": True}],
    )

    with pytest.raises(ValueError, match="names column"):
        load_spider2_tasks(repo)


def test_a_BROADCAST_condition_col_out_of_range_refuses(tmp_path: Path) -> None:
    """The half of the guard the positional tests do not reach.

    Both refusal tests above use positional annotations, and the broadcast
    test's `[1]` is in range on the 2-column fixture -- so moving the range
    check back inside the `all(isinstance(entry, list))` branch left the whole
    suite green while the change's headline behaviour was gone. A flat list is
    broadcast to every accepted result BEFORE upstream's `iloc` sees it, so it
    is out of range there for exactly the same reason.
    """
    repo = _repo(tmp_path)
    _write_eval_annotations(
        repo, [{"instance_id": "local001", "condition_cols": [7], "ignore_order": True}]
    )

    with pytest.raises(ValueError, match="names column"):
        load_spider2_tasks(repo)
