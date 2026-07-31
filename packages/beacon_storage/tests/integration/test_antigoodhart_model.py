"""antigoodhart_findings: append-only, severity-graded."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_storage.models.antigoodhart import (
    AntigoodhartFinding,
    AntigoodhartKind,
    AntigoodhartSeverity,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_finding_create(session: Session) -> None:
    finding = AntigoodhartFinding(
        scan_id=uuid4(),
        item_id=uuid4(),
        team_id=uuid4(),
        suite_id=uuid4(),
        kind=AntigoodhartKind.SQL_IN_QUESTION,
        severity=AntigoodhartSeverity.HIGH,
        description="5-gram overlaps gold SQL",
        evidence={"ngram": "SELECT count FROM orders WHERE", "position": 42},
    )

    session.add(finding)
    session.commit()

    assert finding.id is not None
    assert finding.kind == AntigoodhartKind.SQL_IN_QUESTION


def test_finding_multiple_per_item(session: Session) -> None:
    item_id = uuid4()
    scan_id = uuid4()

    session.add_all(
        [
            AntigoodhartFinding(
                scan_id=scan_id,
                item_id=item_id,
                team_id=uuid4(),
                suite_id=uuid4(),
                kind=AntigoodhartKind.SQL_IN_QUESTION,
                severity=AntigoodhartSeverity.MEDIUM,
                description="sql overlap",
                evidence={},
            ),
            AntigoodhartFinding(
                scan_id=scan_id,
                item_id=item_id,
                team_id=uuid4(),
                suite_id=uuid4(),
                kind=AntigoodhartKind.EVIDENCE_LEAK,
                severity=AntigoodhartSeverity.HIGH,
                description="evidence leak",
                evidence={},
            ),
        ]
    )

    session.commit()
