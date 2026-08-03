"""Importing curated gold, and refusing to import what is not ready.

Beacon does not curate gold; it consumes an export of approved questions. What
this pins is that the import carries the curation forward faithfully — the
reviewed tolerance especially — and declines anything that would quietly change
what a score means.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from beacon_benchmarks.ingest.golden_import import import_golden_package
from beacon_graders.tolerance import DEFAULT_NUMERIC_ABS, Tolerance
from beacon_storage.models.tenancy import Team, User
from beacon_storage.repository.eval_items import EvalItemRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

SUITE = "customer_gold_v1"


@pytest.fixture
def ctx(session: Session) -> tuple[Any, Any]:
    team = Team(name=f"gold-{uuid4().hex[:8]}")
    user = User(email=f"gold-{uuid4().hex[:8]}@example.com", name="G")
    session.add_all([team, user])
    session.flush()
    return team, user


def _question(**over: Any) -> dict[str, Any]:
    q: dict[str, Any] = {
        "tenant_id": "acme",
        "golden_question_id": "gq-1",
        "question": "What was revenue last quarter?",
        "expected_answer": "1200",
        "expected_result": {"kind": "number", "value": 1200.0},
        "status": "approved",
        "version": 3,
        "owner": "analyst@acme",
        "reviewer": "lead@acme",
        "tolerance": {"numeric_abs": 0.5, "numeric_rel": None, "row_order_insensitive": True},
        "semantic_version_refs": ["sem-v7"],
    }
    q.update(over)
    return {"golden_question": q, "history": []}


def _package(*questions: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "golden-v1",
        "tenant_id": "acme",
        "golden_questions": list(questions),
    }


def test_an_approved_question_becomes_an_eval_item(session: Session, ctx: tuple[Any, Any]) -> None:
    team, user = ctx

    report = import_golden_package(
        session, _package(_question()), team_id=team.id, suite=SUITE, created_by=user.id
    )

    assert report.imported == 1
    items = EvalItemRepo(session).list_active(suite=SUITE, team_id=team.id)
    assert len(items) == 1
    assert items[0].item_input["question"] == "What was revenue last quarter?"
    assert items[0].gold_answer == {"answer": 1200.0}


def test_the_curated_tolerance_survives_the_import(session: Session, ctx: tuple[Any, Any]) -> None:
    """The whole point of B23: a reviewed value must not be flattened."""
    team, user = ctx
    import_golden_package(
        session, _package(_question()), team_id=team.id, suite=SUITE, created_by=user.id
    )

    item = EvalItemRepo(session).list_active(suite=SUITE, team_id=team.id)[0]
    tol = Tolerance.model_validate(item.item_metadata["tolerance"])

    assert tol.numeric_abs == 0.5
    assert tol.numeric_abs != DEFAULT_NUMERIC_ABS
    assert tol.row_order_insensitive is True


@pytest.mark.parametrize("status", ["draft", "in_review", "deprecated"])
def test_unapproved_questions_are_not_imported(
    session: Session, ctx: tuple[Any, Any], status: str
) -> None:
    """Draft gold is work in progress; deprecated gold was retired on purpose."""
    team, user = ctx

    report = import_golden_package(
        session,
        _package(_question(status=status)),
        team_id=team.id,
        suite=SUITE,
        created_by=user.id,
    )

    assert report.imported == 0
    assert report.skipped_not_approved == 1
    assert status in report.skipped_reasons[0]


def test_a_question_with_no_usable_expected_result_is_skipped(
    session: Session, ctx: tuple[Any, Any]
) -> None:
    team, user = ctx

    report = import_golden_package(
        session,
        _package(_question(expected_result={"kind": "unsupported"})),
        team_id=team.id,
        suite=SUITE,
        created_by=user.id,
    )

    assert report.imported == 0
    assert report.skipped_no_expected_result == 1


def test_reimporting_the_same_package_is_a_no_op(session: Session, ctx: tuple[Any, Any]) -> None:
    """Re-running an import must not duplicate the corpus a score is over."""
    team, user = ctx
    package = _package(_question())
    import_golden_package(session, package, team_id=team.id, suite=SUITE, created_by=user.id)

    second = import_golden_package(
        session, package, team_id=team.id, suite=SUITE, created_by=user.id
    )

    assert second.imported == 0
    assert second.already_present == 1
    assert len(EvalItemRepo(session).list_active(suite=SUITE, team_id=team.id)) == 1


def test_text_and_table_results_both_map(session: Session, ctx: tuple[Any, Any]) -> None:
    team, user = ctx
    package = _package(
        _question(golden_question_id="t1", expected_result={"kind": "text", "value": "yes"}),
        _question(
            golden_question_id="t2",
            expected_result={"kind": "table", "columns": ["a"], "rows": [[1], [2]]},
        ),
    )

    report = import_golden_package(
        session, package, team_id=team.id, suite=SUITE, created_by=user.id
    )

    assert report.imported == 2
    golds = {
        row.item_metadata["golden_question_id"]: row.gold_answer
        for row in EvalItemRepo(session).list_active(suite=SUITE, team_id=team.id)
    }
    assert golds["t1"] == {"answer": "yes"}
    assert golds["t2"] == {"columns": ["a"], "rows": [[1], [2]]}


def test_provenance_records_that_this_gold_was_imported(
    session: Session, ctx: tuple[Any, Any]
) -> None:
    """A score over customer gold is not a score over a public benchmark."""
    team, user = ctx
    import_golden_package(
        session, _package(_question()), team_id=team.id, suite=SUITE, created_by=user.id
    )

    metadata = EvalItemRepo(session).list_active(suite=SUITE, team_id=team.id)[0].item_metadata

    assert metadata["source"] == "semantic_layer_golden_export"
    assert metadata["golden_question_id"] == "gq-1"
    assert metadata["golden_version"] == 3
    assert metadata["semantic_version_refs"] == ["sem-v7"]


def test_the_fixture_generated_by_veritys_own_serde_imports(
    session: Session, ctx: tuple[Any, Any]
) -> None:
    """The invented-format importer passed every unit test and imported zero
    questions from a real export. This fixture was printed by verity's serde,
    so the test fails if either side's idea of the wire format moves."""
    import json
    from pathlib import Path

    team, user = ctx
    package = json.loads(
        (Path(__file__).parent.parent / "fixtures" / "golden-export-sample.json").read_text()
    )
    suite = f"{SUITE}_serde"

    report = import_golden_package(
        session, package, team_id=team.id, suite=suite, created_by=user.id
    )

    assert report.imported == 1, report.skipped_reasons
    item = EvalItemRepo(session).list_active(suite=suite, team_id=team.id)[0]
    assert item.gold_answer == {"answer": 1200.0}
    assert item.item_metadata["tolerance"] == {"numeric_abs": 0.5, "row_order_insensitive": True}
    assert item.dataset_version == "semantic_layer.golden_questions.v1"
    # And the tolerance, nulls stripped, must actually validate for grading.
    tol = Tolerance.model_validate(item.item_metadata["tolerance"])
    assert tol.numeric_abs == 0.5


def test_a_null_tolerance_field_does_not_break_grading() -> None:
    """Verity serializes an absent bound as null; grading must read it as
    "no curated opinion", not raise at verdict time."""
    tol = Tolerance.model_validate({"numeric_abs": None, "numeric_rel": None})

    assert tol.numeric_abs == DEFAULT_NUMERIC_ABS


def test_dataset_version_defaults_to_the_package_schema_version(
    session: Session, ctx: tuple[Any, Any]
) -> None:
    """A score has to be traceable to the gold it was computed against."""
    team, user = ctx
    import_golden_package(
        session, _package(_question()), team_id=team.id, suite=SUITE, created_by=user.id
    )

    item = EvalItemRepo(session).list_active(suite=SUITE, team_id=team.id)[0]
    assert item.dataset_version == "golden-v1"
