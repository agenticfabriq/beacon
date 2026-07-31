from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_storage.repository.teams import TeamRepo
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_ingest_bird_questions_e2e(db_url: str, session: Session, tmp_path: Path) -> None:
    fixture = [
        {
            "question_id": 9001,
            "db_id": "california_schools",
            "question": "fixture q1",
            "evidence": "",
            "SQL": "SELECT 1",
            "difficulty": "simple",
        },
        {
            "question_id": 9002,
            "db_id": "card_games",
            "question": "fixture q2",
            "evidence": "",
            "SQL": "SELECT 2",
            "difficulty": "moderate",
        },
    ]
    json_path = tmp_path / "mini_dev_postgresql.json"
    json_path.write_text(json.dumps(fixture))

    team_name = f"ingest-test-{uuid4().hex[:8]}"
    TeamRepo(session).create(name=team_name, description="bird ingest e2e")
    session.commit()

    proc = subprocess.run(  # noqa: S603 — trusted args, fixed file path
        [
            sys.executable,
            str(Path("scripts/ingest_bird_questions.py")),
            "--tasks-json",
            str(json_path),
            "--team",
            team_name,
            "--database-url",
            db_url,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

    rows = session.execute(
        text(
            """
            SELECT count(*) FROM eval_items
             WHERE suite = 'bird_minidev_v2'
               AND question_hash LIKE 'bird:v2:90%'
               AND valid_to IS NULL
            """
        )
    ).scalar_one()
    assert rows == 2

    proc2 = subprocess.run(  # noqa: S603 — trusted args, fixed file path
        [
            sys.executable,
            str(Path("scripts/ingest_bird_questions.py")),
            "--tasks-json",
            str(json_path),
            "--team",
            team_name,
            "--database-url",
            db_url,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc2.returncode == 0, proc2.stderr
    assert "skipped=2" in proc2.stdout
