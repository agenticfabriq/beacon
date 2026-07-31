"""Heuristic: distribution skew within a suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_storage.models.antigoodhart import AntigoodhartKind, AntigoodhartSeverity

from beacon_workers.antigoodhart.heuristics.sql_in_question import FindingDraft

if TYPE_CHECKING:
    from uuid import UUID


def scan_suite(
    *,
    suite_id: UUID,
    team_counts: dict[UUID, int],
    threshold: float,
) -> list[FindingDraft]:
    """Flag teams whose item share in a suite exceeds the skew threshold."""
    total = sum(team_counts.values())
    if total == 0:
        return []

    findings: list[FindingDraft] = []
    for team_id, item_count in team_counts.items():
        share = item_count / total
        if share <= threshold:
            continue

        severity = AntigoodhartSeverity.HIGH if share > 0.8 else AntigoodhartSeverity.MEDIUM
        findings.append(
            FindingDraft(
                kind=AntigoodhartKind.DISTRIBUTION_SKEW,
                severity=severity,
                description=(
                    f"team {team_id} owns {share:.0%} of items in this suite "
                    f"(n={item_count}/{total})"
                ),
                evidence={
                    "team_id": str(team_id),
                    "team_share": round(share, 4),
                    "team_item_count": item_count,
                    "suite_item_count": total,
                },
                item_id=None,
                team_id=team_id,
                suite_id=suite_id,
            )
        )
    return findings
