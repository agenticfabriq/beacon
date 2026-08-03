"""The gold importer, reachable.

The importer was built, tested, and called by nothing outside its own tests --
no CLI, no route -- so the seam carrying curated gold from the semantic layer
into beacon could only be reached from a Python REPL. These run the command.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from beacon_storage.models.tenancy import Team, User
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_ui.cli.gold import gold_group
from click.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def gold_team(session: Session) -> tuple[str, str]:
    suffix = uuid4().hex[:8]
    team = Team(name=f"gold-cli-{suffix}")
    user = User(email=f"gold-cli-{suffix}@example.com", name="Importer")
    session.add_all([team, user])
    session.commit()
    return team.name, user.email


def _question(**over: Any) -> dict[str, Any]:
    question: dict[str, Any] = {
        "tenant_id": "acme",
        "golden_question_id": f"gq-{uuid4().hex[:6]}",
        "question": "What was revenue last quarter?",
        "expected_answer": "1200",
        "expected_result": {"Number": {"value": 1200.0}},
        "status": "Approved",
        "version": 3,
        "owner": "analyst@acme",
        "reviewer": "lead@acme",
        "tolerance": {"numeric_abs": 0.5, "row_order_insensitive": True},
        "semantic_version_refs": ["sem-v7"],
    }
    question.update(over)
    return {"golden_question": question, "history": []}


def _package(tmp_path: Path, *questions: dict[str, Any]) -> Path:
    path = tmp_path / "package.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "golden-v1",
                "tenant_id": "acme",
                "golden_questions": list(questions),
            }
        ),
        encoding="utf-8",
    )
    return path


def _run(package: Path, team: str, actor: str, suite: str, db_url: str) -> Any:
    return CliRunner().invoke(
        gold_group,
        ["import", "--package", str(package), "--team", team, "--suite", suite, "--as", actor],
        env={"DATABASE_URL": db_url},
    )


def test_the_command_imports_approved_gold(
    tmp_path: Path, session: Session, gold_team: tuple[str, str], db_url: str
) -> None:
    team_name, actor = gold_team
    suite = f"cli_gold_{uuid4().hex[:6]}"

    result = _run(_package(tmp_path, _question()), team_name, actor, suite, db_url)

    assert result.exit_code == 0, result.output
    assert "imported        1" in result.output
    items = EvalItemRepo(session).list_active(suite=suite)
    assert len(items) == 1


def test_it_reports_what_it_refused(
    tmp_path: Path, gold_team: tuple[str, str], db_url: str
) -> None:
    """A package that imports nothing because it is all in review must say so."""
    team_name, actor = gold_team
    package = _package(tmp_path, _question(status="Draft"), _question(status="InReview"))

    result = _run(package, team_name, actor, f"cli_gold_{uuid4().hex[:6]}", db_url)

    assert result.exit_code == 0, result.output
    assert "imported        0" in result.output
    assert "not approved    2" in result.output
    assert "Draft" in result.output


def test_the_curated_tolerance_survives_the_command(
    tmp_path: Path, session: Session, gold_team: tuple[str, str], db_url: str
) -> None:
    team_name, actor = gold_team
    suite = f"cli_gold_{uuid4().hex[:6]}"

    _run(_package(tmp_path, _question()), team_name, actor, suite, db_url)

    item = EvalItemRepo(session).list_active(suite=suite)[0]
    assert item.item_metadata["tolerance"]["numeric_abs"] == 0.5


def test_an_unknown_team_is_refused(
    tmp_path: Path, gold_team: tuple[str, str], db_url: str
) -> None:
    _, actor = gold_team

    result = _run(_package(tmp_path, _question()), "no-such-team", actor, "s", db_url)

    assert result.exit_code != 0
    assert "team no-such-team not found" in result.output


def test_an_unknown_actor_is_refused(
    tmp_path: Path, gold_team: tuple[str, str], db_url: str
) -> None:
    team_name, _ = gold_team

    result = _run(_package(tmp_path, _question()), team_name, "nobody@example.com", "s", db_url)

    assert result.exit_code != 0
    assert "not found" in result.output


def test_a_file_that_is_not_json_is_refused(
    tmp_path: Path, gold_team: tuple[str, str], db_url: str
) -> None:
    team_name, actor = gold_team
    path = tmp_path / "package.json"
    path.write_text("not json", encoding="utf-8")

    result = _run(path, team_name, actor, "s", db_url)

    assert result.exit_code != 0
    assert "not valid JSON" in result.output
