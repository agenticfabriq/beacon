"""The transport rule has multiple homes; this test makes them agree or fail.

Decimal -> float, temporal -> ISO, nothing else coerced. The rule lives in the
fs payments loader (``_json_safe``), in the grader's comparison-time
canonicalization (``canonicalize_cell``), and in the fs payments SUT's push
path (``_transport_safe``). Three copies that silently drift would put a
string beside a float and grade a plausible FAIL -- the manufactured null
that is hardest to spot, because it looks like a real miss (measured:
``jsonable(Decimal("40505.25"))`` is the string, and it never matches the
float gold under type-strict cells).

Consolidation into one importable home (beacon_runner, the bottom of the
dependency graph) is agreed and pending; until then, agreement is enforced
here rather than assumed. When the SUT's copy merges, add it to _RULES.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from beacon_benchmarks.fs_payments.ingest_items import _json_safe
from beacon_graders.comparison import canonicalize_cell

_RULES = {
    "loader._json_safe": _json_safe,
    "grader.canonicalize_cell": canonicalize_cell,
}

# The primitive domain the shared rule covers. canonicalize_cell additionally
# strips strings at comparison time; that is comparison behavior, not
# transport, so bare unpadded strings are the overlap asserted here.
_CASES = [
    (Decimal("40505.25"), 40505.25),
    (Decimal("12"), 12.0),
    (datetime(2026, 3, 17, 9, 30, tzinfo=UTC), "2026-03-17T09:30:00+00:00"),
    (date(1947, 3, 17), "1947-03-17"),
    ("0075", "0075"),  # a textual code stays textual: padding is the value
    (True, True),  # a truth value never becomes 1
    (None, None),  # NULL stays NULL, never 0, never ""
    (12, 12),
    (1.5, 1.5),
]


@pytest.mark.parametrize(("value", "expected"), _CASES, ids=[repr(v) for v, _ in _CASES])
def test_every_home_of_the_transport_rule_agrees(value: object, expected: object) -> None:
    for name, rule in _RULES.items():
        got = rule(value)
        assert got == expected, f"{name}({value!r}) -> {got!r}, expected {expected!r}"
        assert type(got) is type(expected), (
            f"{name}({value!r}) -> {type(got).__name__}, expected {type(expected).__name__}: "
            "a string beside a float grades a plausible FAIL"
        )
