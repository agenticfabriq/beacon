"""Beacon's half of the shared grading conformance suite.

Verity decides what "correct" means for a question; beacon decides who is more
often correct. Nothing tested that the two agreed about the same answer, and
they did not: beacon called 45 correct answers wrong for want of a relative
bound, and verity applied a curated tolerance to a scalar while comparing the
same number inside a table byte-exactly.

The case file is byte-identical in both repos and its digest is pinned here, so
editing one copy and not the other fails both suites.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from beacon_graders.comparison import compare_rows
from beacon_graders.tolerance import Tolerance

CONTRACT = Path(__file__).parent / "conformance" / "grading-conformance-v1.json"

# Bump only by editing BOTH copies of the file and BOTH pinned digests. A shared
# contract that can drift silently is not shared.
CONTRACT_SHA256 = "3ecadb322438db5d8075d58f705634449f6435601c88afc7a21ebe5294ff96ed"


def _contract() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return loaded


def _cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = _contract()["cases"]
    return cases


def _tolerance(case: dict[str, Any]) -> Tolerance:
    """The case's own tolerance, over the defaults both graders ship."""
    merged = dict(_contract()["default_tolerance"])
    merged.update(case.get("tolerance") or {})
    return Tolerance.model_validate(merged)


def test_the_contract_has_not_drifted_from_the_other_repo() -> None:
    digest = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()

    assert digest == CONTRACT_SHA256, (
        "grading-conformance-v1.json changed. Update BOTH repos' copies and BOTH "
        f"pinned digests, or the two graders are no longer testing the same contract. "
        f"New digest: {digest}"
    )


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["id"]))
def test_beacon_agrees_with_the_shared_contract(case: dict[str, Any]) -> None:
    tolerance = _tolerance(case)

    if case["kind"] == "number":
        matched = tolerance.numbers_match(float(case["actual"]), float(case["expected"]))
    elif case["kind"] == "text":
        matched = case["actual"] == case["expected"]
    else:
        actual = [tuple(row) for row in case["actual"]]
        expected = [tuple(row) for row in case["expected"]]
        matched = compare_rows(
            actual,
            expected,
            not tolerance.row_order_insensitive,
            tolerance,
        )

    assert matched is case["match"], case["why"]


def test_every_case_explains_itself() -> None:
    """A contract case with no reason cannot be argued with when it fails."""
    assert all(case.get("why") for case in _cases())
