"""Reading one criterion's score out of a judge's JSON.

Shared by the two LLM-judge graders because they had the same hole in the same
shape, and one of them had already fixed half of it.
"""

from __future__ import annotations

import math


def criterion_score(payload: object) -> tuple[float, str] | None:
    """Return a criterion's (score, justification), or None if it was not scored.

    Guards both levels. The criterion object can be absent or the wrong type,
    and it can be present with no usable score -- ``{}``, a justification with
    no ``score`` key, ``{"score": null}``, ``{"score": "n/a"}``. Every one of
    those used to coerce to 0.0 and become indistinguishable from a judge that
    read the answer and scored it zero.
    """
    if not isinstance(payload, dict) or "score" not in payload:
        return None
    justification = str(payload.get("justification", ""))
    raw = payload["score"]
    # `True` is an int in Python and would score 1.0; a bool is not a score.
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # NaN clamps to the MAXIMUM -- `min(1.0, nan)` is nan and `max(0.0, nan)`
    # is nan, and the stdlib decoder `extract_json` uses accepts a bare `NaN`
    # literal -- so a judge emitting one would hand out a perfect score.
    if not math.isfinite(value):
        return None
    return max(0.0, min(1.0, value)), justification
