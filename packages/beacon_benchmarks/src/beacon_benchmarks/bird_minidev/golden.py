"""BIRD golden-question loader."""

from __future__ import annotations

import json
import logging
from importlib.resources import files
from typing import TYPE_CHECKING, Any, cast

import yaml

from beacon_benchmarks.base.adapter import EvalItemDraft
from beacon_benchmarks.base.errors import SqlTranslationError
from beacon_benchmarks.ingest.sql_translator import translate_sqlite_to_postgres

if TYPE_CHECKING:
    from pathlib import Path


log = logging.getLogger(__name__)

SUITE = "bird_minidev_v2"
DATASET_VERSION = "v2-2025-07-22"


def translate_gold_sql_or_flag(sql: str) -> tuple[str, str]:
    """Return translated SQL plus the translator status tag."""
    try:
        return translate_sqlite_to_postgres(sql), "rule_based_v1"
    except SqlTranslationError as exc:
        log.warning("SQL flagged for manual review: %s", exc)
        return sql, "manual_review"


def _load_tasks_json(bird_root: Path) -> list[dict[str, Any]]:
    """Prefer the PostgreSQL-dialect variant if present; otherwise SQLite."""
    postgres_path = bird_root / "mini_dev_data" / "mini_dev_postgresql.json"
    sqlite_path = bird_root / "mini_dev_data" / "mini_dev_sqlite.json"
    if postgres_path.exists():
        return cast(
            "list[dict[str, Any]]",
            json.loads(postgres_path.read_text(encoding="utf-8")),
        )
    if sqlite_path.exists():
        return cast(
            "list[dict[str, Any]]",
            json.loads(sqlite_path.read_text(encoding="utf-8")),
        )
    raise FileNotFoundError(f"BIRD task JSON not found under {bird_root}/mini_dev_data/")


def build_drafts(bird_root: Path, *, selected_dbs: list[str]) -> list[EvalItemDraft]:
    """Build eval-item drafts for selected BIRD databases."""
    tasks = _load_tasks_json(bird_root)
    selected = set(selected_dbs)
    drafts: list[EvalItemDraft] = []

    for task in tasks:
        db_id = str(task["db_id"])
        if db_id not in selected:
            continue
        sqlite_sql = str(task["SQL"])
        postgres_sql, translator = translate_gold_sql_or_flag(sqlite_sql)
        question_id = task["question_id"]
        difficulty = task.get("difficulty")
        evidence = str(task.get("evidence", ""))

        drafts.append(
            EvalItemDraft(
                suite=SUITE,
                dataset_version=DATASET_VERSION,
                query={
                    "question": task["question"],
                    "db": db_id,
                    "evidence": evidence,
                },
                context={
                    "postgres_schema": f"bird_{db_id}",
                    "ontology_path": (f"beacon_benchmarks/bird_minidev/ontology/{db_id}.yaml"),
                },
                ground_truth={
                    "sql_sqlite": sqlite_sql,
                    "sql_postgres": postgres_sql,
                },
                ground_truth_meta={
                    "source": "BIRD-Mini-Dev-V2",
                    "question_id": question_id,
                    "translator": translator,
                },
                metadata={
                    "db_id": db_id,
                    "question_id": question_id,
                    "difficulty": difficulty,
                    "evidence": evidence,
                    "translator": translator,
                },
                difficulty=cast("str | None", difficulty),
                item_external_id=f"bird:{question_id}",
            )
        )

    return drafts


def load_ontology(db_id: str) -> dict[str, Any]:
    """Read the YAML ontology scaffold for a BIRD database."""
    text = (
        files("beacon_benchmarks.bird_minidev.ontology")
        .joinpath(f"{db_id}.yaml")
        .read_text(encoding="utf-8")
    )
    return cast("dict[str, Any]", yaml.safe_load(text))
