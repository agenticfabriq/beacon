"""The transport boundary's primitives, in the one package everything reaches.

Two rules and one number kept growing extra homes -- the fs payments loader,
the grader's comparison-time canonicalization, an SUT's push path -- and three
copies that silently drift put a string beside a float and grade a plausible
FAIL nobody audits. beacon_runner is the bottom of the dependency graph, so
the primitives live here and everything imports upward; the agreement test in
beacon_benchmarks holds every home to these semantics.

The rules are deliberately minimal: Decimal becomes float, temporal values
become ISO strings, and NOTHING ELSE is coerced. A genuinely textual code
("0075", a bucket label) must never become a number here -- comparison
type-strictness is what makes that safe, and a transport layer that coerces
strings would silently undo it.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

# The most rows a push may carry -- BIRD's own cap on gold result sets. More
# is truncated (recorded), never an error: the true count still grades. One
# policy; the grader enforces it at grading, an SUT may enforce it at push,
# and both use this constant so they cannot disagree.
MAX_PUSHED_ROWS = 1000


def transport_value(value: Any) -> Any:
    """A value JSONB can hold, without becoming stringly where it matters."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return value
