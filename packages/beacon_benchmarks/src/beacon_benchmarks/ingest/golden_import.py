"""Import a curated golden-question package into beacon's gold store.

Beacon does not curate gold. Curation — review workflow, versioning,
deprecation, tolerance — lives in the semantic layer, which exports a package
of approved questions. This turns that package into eval items so a customer
can benchmark against the same gold their production answers are graded on.

What deliberately does *not* happen here: no editing, no promotion, no tiering.
The gold store is a destination for imports, not an authoring surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.repository.eval_items import EvalItemRepo

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

# Only reviewed gold is worth benchmarking against. Draft and in-review
# questions are somebody's work in progress; deprecated ones were retired on
# purpose, and importing either would quietly change what a score means.
_IMPORTABLE_STATUSES = frozenset({"approved", "Approved"})


@dataclass
class GoldenImportReport:
    """What an import did, and what it declined to do."""

    imported: int = 0
    skipped_not_approved: int = 0
    skipped_no_expected_result: int = 0
    already_present: int = 0
    skipped_reasons: list[str] = field(default_factory=list)

    @property
    def considered(self) -> int:
        return (
            self.imported
            + self.skipped_not_approved
            + self.skipped_no_expected_result
            + self.already_present
        )


def _gold_answer(expected: Any) -> dict[str, Any] | None:
    """Map an exported expected result onto beacon's gold shape.

    The semantic layer stores an expected *result* — text, a number, or a table.
    Beacon's benchmark adapters store gold *SQL* and execute it. Both are
    legitimate; this importer targets the former, which is what customer-curated
    gold looks like.
    """
    if not isinstance(expected, dict):
        return None
    # Serde tags the enum by variant name.
    if "Text" in expected:
        return {"answer": expected["Text"].get("value")}
    if "Number" in expected:
        return {"answer": expected["Number"].get("value")}
    if "Table" in expected:
        table = expected["Table"]
        return {"columns": table.get("columns", []), "rows": table.get("rows", [])}
    return None


def _tolerance(raw: Any) -> dict[str, Any] | None:
    """Carry the curated tolerance across so beacon does not flatten it."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    if isinstance(raw.get("numeric_abs"), int | float):
        out["numeric_abs"] = float(raw["numeric_abs"])
    if isinstance(raw.get("row_order_insensitive"), bool):
        out["row_order_insensitive"] = raw["row_order_insensitive"]
    return out or None


def import_golden_package(
    session: Session,
    package: dict[str, Any],
    *,
    team_id: UUID,
    suite: str,
    created_by: UUID,
    dataset_version: str | None = None,
) -> GoldenImportReport:
    """Import approved golden questions from ``package`` as eval items.

    ``dataset_version`` defaults to the package's ``schema_version`` so a score
    can be traced back to the gold it was computed against.
    """
    report = GoldenImportReport()
    version = dataset_version or str(package.get("schema_version") or "golden-v1")
    repo = EvalItemRepo(session)
    existing = {
        str(row.item_metadata.get("golden_question_id"))
        for row in repo.list_active(suite=suite, team_id=team_id)
        if row.item_metadata.get("golden_question_id")
    }

    for record in package.get("golden_questions") or []:
        question = record.get("golden_question") if isinstance(record, dict) else None
        if not isinstance(question, dict):
            continue

        gq_id = str(question.get("golden_question_id") or "")
        status = str(question.get("status") or "")
        if status not in _IMPORTABLE_STATUSES:
            report.skipped_not_approved += 1
            report.skipped_reasons.append(f"{gq_id}: status {status!r} is not approved")
            continue

        gold = _gold_answer(question.get("expected_result"))
        if gold is None:
            report.skipped_no_expected_result += 1
            report.skipped_reasons.append(f"{gq_id}: no usable expected_result")
            continue

        if gq_id in existing:
            report.already_present += 1
            continue

        metadata: dict[str, Any] = {
            "golden_question_id": gq_id,
            "golden_version": question.get("version"),
            "owner": question.get("owner"),
            "reviewer": question.get("reviewer"),
            # Provenance: these came from curated gold, not from a benchmark
            # corpus, and the distinction matters when comparing scores.
            "source": "semantic_layer_golden_export",
            "semantic_version_refs": question.get("semantic_version_refs") or [],
        }
        if (tol := _tolerance(question.get("tolerance"))) is not None:
            metadata["tolerance"] = tol

        repo.create(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite=suite,
            team_id=team_id,
            dataset_version=version,
            item_input={"question": question.get("question")},
            gold_answer=gold,
            item_metadata=metadata,
            created_by=created_by,
        )
        existing.add(gq_id)
        report.imported += 1

    return report
