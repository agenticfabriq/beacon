"""Repository for anti-Goodhart findings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import desc, select

from beacon_storage.models.antigoodhart import (
    AntigoodhartFinding,
    AntigoodhartKind,
    AntigoodhartSeverity,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class AntigoodhartRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        scan_id: UUID,
        item_id: UUID | None,
        team_id: UUID | None,
        suite_id: UUID | None,
        kind: AntigoodhartKind,
        severity: AntigoodhartSeverity,
        description: str,
        evidence: dict[str, Any],
    ) -> AntigoodhartFinding:
        """Persist a new anti-Goodhart finding and return it."""
        finding = AntigoodhartFinding(
            scan_id=scan_id,
            item_id=item_id,
            team_id=team_id,
            suite_id=suite_id,
            kind=kind,
            severity=severity,
            description=description,
            evidence=evidence,
        )
        self.session.add(finding)
        self.session.flush()
        return finding

    def list_for_scan(self, scan_id: UUID) -> list[AntigoodhartFinding]:
        """Return every finding recorded under ``scan_id``."""
        return list(
            self.session.scalars(
                select(AntigoodhartFinding).where(AntigoodhartFinding.scan_id == scan_id)
            )
        )

    def list_for_item(self, item_id: UUID) -> list[AntigoodhartFinding]:
        """Return findings for ``item_id`` ordered newest first."""
        return list(
            self.session.scalars(
                select(AntigoodhartFinding)
                .where(AntigoodhartFinding.item_id == item_id)
                .order_by(desc(AntigoodhartFinding.created_at), desc(AntigoodhartFinding.id))
            )
        )

    def latest_scan_id(self, *, team_id: UUID | None, suite_id: UUID | None) -> UUID | None:
        """Return the most recent scan id scoped to the given team/suite, or None."""
        stmt = select(AntigoodhartFinding.scan_id)
        if team_id is None:
            stmt = stmt.where(AntigoodhartFinding.team_id.is_(None))
        else:
            stmt = stmt.where(AntigoodhartFinding.team_id == team_id)
        if suite_id is None:
            stmt = stmt.where(AntigoodhartFinding.suite_id.is_(None))
        else:
            stmt = stmt.where(AntigoodhartFinding.suite_id == suite_id)

        return self.session.scalar(
            stmt.order_by(
                desc(AntigoodhartFinding.created_at),
                desc(AntigoodhartFinding.id),
            ).limit(1)
        )
