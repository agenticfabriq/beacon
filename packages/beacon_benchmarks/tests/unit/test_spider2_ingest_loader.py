from __future__ import annotations

import json
from typing import TYPE_CHECKING

from beacon_benchmarks.spider2_lite.ingest_items import load_spider2_tasks

if TYPE_CHECKING:
    from pathlib import Path


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
    (exec_dir / "local001_a.csv").write_text("n\n3527\n", encoding="utf-8")
    (exec_dir / "local001_b.csv").write_text("total\n3527\n", encoding="utf-8")
    (exec_dir / "local002.csv").write_text("orders\n830\n", encoding="utf-8")
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
    assert first.accepted_results[0] == {"columns": ["n"], "rows": [["3527"]]}
    assert first.accepted_results[1] == {"columns": ["total"], "rows": [["3527"]]}


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


def test_a_case_with_no_published_result_still_loads(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    records = json.loads(json.dumps({"instance_id": "local003", "db": "chinook",
                                     "question": "Q", "external_knowledge": None}))
    with (repo / "spider2-lite.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n" + json.dumps(records))

    task = next(t for t in load_spider2_tasks(repo) if t.instance_id == "local003")

    # Loading must not hide it -- the runner reports it as an ERROR rather than
    # silently passing a case nothing can grade.
    assert task.accepted_results == []
