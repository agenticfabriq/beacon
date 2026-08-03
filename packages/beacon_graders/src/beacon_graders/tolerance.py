"""How close two results have to be to count as the same answer.

Modelled on the tolerance curated alongside gold in the semantic layer, so the
two graders in the portfolio can agree on the same question rather than
disagreeing by construction. Two fields, both optional:

* ``numeric_abs`` -- the largest absolute difference between two numbers that
  still counts as equal. Absolute rather than a decimal-place count: a candidate
  casting to DECIMAL and a gold casting to REAL differ by an amount, not by a
  number of digits, and rounding to N places disagrees with itself near a
  rounding boundary.
* ``numeric_rel`` -- the same bound as a fraction of the gold value. An absolute
  bound alone is unsatisfiable at scale: a REAL holds about seven significant
  digits, so a nine-digit answer cannot land within 5e-7 of a double-precision
  gold no matter how right it is. Measured on the re-graded corpus, 45 of 350
  value mismatches were pure float32 representation noise at a relative
  difference below 1e-6 -- correct answers beacon was calling wrong.
* ``row_order_insensitive`` -- whether row order carries meaning. Beacon infers
  this from whether the gold query has an ORDER BY; curated gold can say so
  outright, and an explicit value wins.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# The absolute equivalent of the 6-decimal rounding this replaces. Wide enough
# to absorb a DECIMAL-vs-REAL cast, tight enough that two genuinely different
# answers stay different.
DEFAULT_NUMERIC_ABS = 5e-7
# About eight times float32 epsilon: wide enough for representation noise in a
# single cast, narrow enough that accumulated error is still a failure. On the
# corpus this admits the 45 representation cases and none of the 122 genuinely
# different numbers. Values between the two -- float32 error accumulated over a
# large aggregate -- stay failures under the default; a suite that wants to
# accept them says so per item, which is what curated tolerance is for.
DEFAULT_NUMERIC_REL = 1e-6

_METADATA_KEY = "tolerance"


class Tolerance(BaseModel):
    """Per-item comparison tolerance, defaulting to beacon's own."""

    model_config = ConfigDict(extra="ignore")

    numeric_abs: float = Field(default=DEFAULT_NUMERIC_ABS, ge=0.0)
    numeric_rel: float = Field(default=DEFAULT_NUMERIC_REL, ge=0.0)
    # None means "no curated opinion" -- fall back to inferring from the gold
    # query, which is what beacon has always done.
    row_order_insensitive: bool | None = None

    @classmethod
    def for_item(cls, item: Any) -> Tolerance:
        """Read the tolerance curated for an item, or beacon's default.

        Imported gold carries its reviewed tolerance in item metadata. Flattening
        that into a global constant would silently override a decision someone
        made deliberately about a specific question.
        """
        metadata = getattr(item, "metadata", None)
        if not isinstance(metadata, dict):
            return cls()
        raw = metadata.get(_METADATA_KEY)
        if not isinstance(raw, dict):
            return cls()
        return cls.model_validate(raw)

    def numbers_match(self, candidate: float, gold: float) -> bool:
        """Return whether two numbers are equal within this tolerance.

        Either bound satisfies it: the absolute one carries small magnitudes,
        where a relative bound would be vanishingly tight, and the relative one
        carries large magnitudes, where an absolute bound is unsatisfiable.

        Except between whole numbers, which are compared exactly. A relative
        bound on a large integer admits being off by one, and a count, a year or
        an id is either right or wrong -- 1234567891 rows is not 1234567890 rows
        to within 8e-10. Found by the shared conformance suite, against this
        method, the day the relative bound was added.
        """
        difference = abs(candidate - gold)
        if float(candidate).is_integer() and float(gold).is_integer():
            return difference == 0.0
        return difference <= self.numeric_abs or difference <= self.numeric_rel * abs(gold)
