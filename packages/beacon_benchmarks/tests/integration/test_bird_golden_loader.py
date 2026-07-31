"""BIRD golden-question loader: BIRD JSON to EvalItemDraft list."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from beacon_benchmarks.bird_minidev.golden import build_drafts, load_ontology

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def bird_root(tmp_path: Path) -> Path:
    root = tmp_path / "bird"
    (root / "mini_dev_data").mkdir(parents=True)
    tasks: list[dict[str, Any]] = [
        {
            "question_id": 0,
            "db_id": "california_schools",
            "question": "Highest free rate?",
            "evidence": "free rate = `Free Meal Count (K-12)` / `Enrollment (K-12)`",
            "SQL": "SELECT `Free Meal Count (K-12)` / `Enrollment (K-12)` FROM frpm",
            "difficulty": "simple",
        },
        {
            "question_id": 1,
            "db_id": "card_games",
            "question": "Count of mythic cards?",
            "evidence": "",
            "SQL": "SELECT COUNT(*) FROM cards WHERE rarity = 'mythic'",
            "difficulty": "moderate",
        },
        {
            "question_id": 99,
            "db_id": "movies",
            "question": "x",
            "evidence": "",
            "SQL": "SELECT 1",
            "difficulty": "simple",
        },
    ]
    (root / "mini_dev_data" / "mini_dev_postgresql.json").write_text(
        json.dumps(tasks),
        encoding="utf-8",
    )
    return root


def test_build_drafts_filters_to_selected_dbs(bird_root: Path) -> None:
    drafts = build_drafts(bird_root, selected_dbs=["california_schools", "card_games"])

    assert len(drafts) == 2
    assert all(draft.metadata["db_id"] in {"california_schools", "card_games"} for draft in drafts)


def test_build_drafts_records_question_id_and_evidence(bird_root: Path) -> None:
    drafts = build_drafts(bird_root, selected_dbs=["california_schools"])
    draft = drafts[0]

    assert draft.metadata["question_id"] == 0
    assert draft.query["evidence"].startswith("free rate")
    assert draft.suite == "bird_minidev_v2"
    assert draft.dataset_version == "v2-2025-07-22"


def test_build_drafts_stores_postgres_translation(bird_root: Path) -> None:
    drafts = build_drafts(bird_root, selected_dbs=["california_schools"])
    postgres_sql = drafts[0].ground_truth["sql_postgres"]

    assert '"Free Meal Count (K-12)"' in postgres_sql
    assert "`" not in postgres_sql


def test_build_drafts_flags_untranslatable_with_translator_tag(bird_root: Path) -> None:
    tasks_path = bird_root / "mini_dev_data" / "mini_dev_postgresql.json"
    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    tasks.append(
        {
            "question_id": 2,
            "db_id": "california_schools",
            "question": "When?",
            "evidence": "",
            "SQL": "SELECT DATETIME(created_at) FROM schools",
            "difficulty": "challenging",
        }
    )
    tasks_path.write_text(json.dumps(tasks), encoding="utf-8")

    drafts = build_drafts(bird_root, selected_dbs=["california_schools"])
    flagged = [draft for draft in drafts if draft.metadata.get("translator") == "manual_review"]

    assert len(flagged) == 1
    assert flagged[0].metadata["question_id"] == 2


def test_load_ontology_for_each_selected_db() -> None:
    for db_id in (
        "california_schools",
        "card_games",
        "european_football_2",
        "formula_1",
        "superhero",
    ):
        ontology = load_ontology(db_id)
        assert ontology["db_id"] == db_id
        assert "tables" in ontology
