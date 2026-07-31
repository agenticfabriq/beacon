from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from beacon_benchmarks.bird_minidev.ingest_items import BirdTask, load_bird_tasks

if TYPE_CHECKING:
    from pathlib import Path


def test_load_bird_tasks_filters_to_selected_dbs(tmp_path: Path) -> None:
    payload = [
        {
            "question_id": 1,
            "db_id": "california_schools",
            "question": "Q1",
            "evidence": "E1",
            "SQL": "SELECT 1",
            "difficulty": "simple",
        },
        {
            "question_id": 2,
            "db_id": "thrombosis_prediction",
            "question": "Q2",
            "evidence": "",
            "SQL": "SELECT 2",
            "difficulty": "moderate",
        },
        {
            "question_id": 3,
            "db_id": "card_games",
            "question": "Q3",
            "evidence": "E3",
            "SQL": "SELECT 3",
            "difficulty": "challenging",
        },
    ]
    path = tmp_path / "mini_dev_postgresql.json"
    path.write_text(json.dumps(payload))

    tasks = load_bird_tasks(path, selected_dbs=("california_schools", "card_games"))

    assert tasks == [
        BirdTask(
            question_id=1,
            db_id="california_schools",
            question="Q1",
            evidence="E1",
            sql="SELECT 1",
            difficulty="simple",
        ),
        BirdTask(
            question_id=3,
            db_id="card_games",
            question="Q3",
            evidence="E3",
            sql="SELECT 3",
            difficulty="challenging",
        ),
    ]


def test_load_bird_tasks_raises_when_file_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_bird_tasks(tmp_path / "missing.json", selected_dbs=("any",))
