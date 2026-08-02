"""How close two results have to be to count as the same answer.

Modelled on the tolerance curated alongside gold in the semantic layer, so the
two graders in the portfolio can agree on the same question rather than
disagreeing by construction. Two fields, both optional:

* ``numeric_abs`` -- the largest absolute difference between two numbers that
  still counts as equal. Absolute rather than a decimal-place count: a candidate
  casting to DECIMAL and a gold casting to REAL differ by an amount, not by a
  number of digits, and rounding to N places disagrees with itself near a
  rounding boundary.
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

_METADATA_KEY = "tolerance"


class Tolerance(BaseModel):
    """Per-item comparison tolerance, defaulting to beacon's own."""

    model_config = ConfigDict(extra="ignore")

    numeric_abs: float = Field(default=DEFAULT_NUMERIC_ABS, ge=0.0)
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
        """Return whether two numbers are equal within this tolerance."""
        return abs(candidate - gold) <= self.numeric_abs
