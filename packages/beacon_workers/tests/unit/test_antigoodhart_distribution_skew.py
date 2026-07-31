"""Distribution-skew anti-Goodhart heuristic."""

from __future__ import annotations

from uuid import uuid4

from beacon_storage.models.antigoodhart import AntigoodhartSeverity
from beacon_workers.antigoodhart.heuristics.distribution_skew import scan_suite


def test_balanced_suite_no_finding() -> None:
    team_a, team_b, team_c = uuid4(), uuid4(), uuid4()
    counts = {team_a: 10, team_b: 10, team_c: 10}

    findings = scan_suite(suite_id=uuid4(), team_counts=counts, threshold=0.6)

    assert findings == []


def test_dominant_team_medium() -> None:
    team_a, team_b = uuid4(), uuid4()
    counts = {team_a: 7, team_b: 3}

    findings = scan_suite(suite_id=uuid4(), team_counts=counts, threshold=0.6)

    assert len(findings) == 1
    assert findings[0].severity == AntigoodhartSeverity.MEDIUM
    assert findings[0].team_id == team_a


def test_dominant_team_high() -> None:
    team_a, team_b = uuid4(), uuid4()
    counts = {team_a: 90, team_b: 10}

    findings = scan_suite(suite_id=uuid4(), team_counts=counts, threshold=0.6)

    assert len(findings) == 1
    assert findings[0].severity == AntigoodhartSeverity.HIGH


def test_below_threshold_no_finding() -> None:
    team_a, team_b = uuid4(), uuid4()
    counts = {team_a: 55, team_b: 45}

    findings = scan_suite(suite_id=uuid4(), team_counts=counts, threshold=0.6)

    assert findings == []


def test_empty_suite_no_finding() -> None:
    findings = scan_suite(suite_id=uuid4(), team_counts={}, threshold=0.6)

    assert findings == []


def test_single_team_suite_high() -> None:
    team_a = uuid4()
    counts = {team_a: 50}

    findings = scan_suite(suite_id=uuid4(), team_counts=counts, threshold=0.6)

    assert len(findings) == 1
    assert findings[0].severity == AntigoodhartSeverity.HIGH
