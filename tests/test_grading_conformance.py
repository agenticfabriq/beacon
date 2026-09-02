"""Beacon's half of the shared grading conformance suite.

Verity decides what "correct" means for a question; beacon decides who is more
often correct. Nothing tested that the two agreed about the same answer, and
they did not: beacon called 45 correct answers wrong for want of a relative
bound, and verity applied a curated tolerance to a scalar while comparing the
same number inside a table byte-exactly.

The case file is byte-identical in both repos and its digest is pinned here.
That pin catches an edit to THIS repo's copy that forgot to update THIS repo's
constant. It cannot see the other repo BY CONSTRUCTION: it hashes the file
beside it against the literal above it, and would still not read the other copy
if both repos were checked out side by side. So checking out both in CI fixes
nothing -- the comparison that is missing is between the two COPIES, and no
test in either repo makes it. (Verified on both halves: verity's
`grading_conformance.rs` hashes an ``include_str!`` of its own sibling file
against its own literal, exactly as this does.)

Editing one copy fails ONE suite. Editing a copy together with its own pin
while forgetting the other repo passes BOTH, with two different contracts,
which is the silent drift this file used to claim was impossible. Keeping the
two equal is a human protocol; see the sibling README for what is and is not
enforced.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from beacon_graders.comparison import compare_rows
from beacon_graders.tolerance import Tolerance

CONTRACT = Path(__file__).parent / "conformance" / "grading-conformance-v2.json"

# Bump only by editing BOTH copies of the file and BOTH pinned digests, then
# `shasum -a 256` both copies and compare. Nothing here can check that for you.
# shasum and not diff: diff is silent on a match AND on the same file passed
# twice, so a mis-paste reads as a pass. See tests/conformance/README.md.
CONTRACT_SHA256 = "e259b1e5764c999291be6740ab3172e0d910798dea04cfd59f29f63caba84314"


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


def test_the_contract_has_not_changed_without_its_pin() -> None:
    """Named for what it checks. It was called
    ``test_the_contract_has_not_drifted_from_the_other_repo``, which promised a
    cross-repo comparison the assertion never made -- so the one test that
    WOULD report a real drift reported it under a name claiming coverage that
    does not exist."""
    digest = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()

    assert digest == CONTRACT_SHA256, (
        "grading-conformance-v2.json changed without its pin. Update BOTH repos' "
        "copies and BOTH pinned digests, then `shasum -a 256` both copies and "
        "compare -- no test does that, in either repo. See "
        "tests/conformance/README.md. "
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
