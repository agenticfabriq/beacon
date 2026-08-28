"""Reading one criterion's score out of a judge's JSON.

Shared by the two LLM-judge graders because they had the same hole in the same
shape, and one of them had already fixed half of it.
"""

from __future__ import annotations

import math

# Diagnostic echoes of malformed judge output are bounded; the judge's own
# justification is NOT. An echo is a rendering we chose, reproducible from the
# reply, and `hierarchical_rubric` repeats its container note across every
# criterion in the rubric. A justification is the audit record itself, with no
# other copy -- `raw_output` carries only the model version and token counts,
# and `JudgeCache` is an in-process LRU that is never persisted -- so cutting
# one destroys evidence to save column width, which is the wrong trade.
_MAX_QUOTED = 120


def cut(rendered: str, limit: int = _MAX_QUOTED) -> str:
    """Bound a quoted value and SAY when it was bounded.

    A silent cut is its own small overclaim: ``{'score': 12345678`` reads as a
    complete value the judge never sent. The marker is the difference between
    showing less and showing something else.
    """
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[:limit]}... (cut, {len(rendered)} chars)"


def quote_judge_value(value: object) -> str:
    """Render a malformed judge value for an operator, bounded and marked."""
    return cut(repr(value))


def _as_words(value: object) -> str:
    """Render a judge's justification without inventing one.

    ``str()`` alone turns a JSON null into the truthy literal "None" and shows
    it to the operator as something the judge said. Dropping every non-string
    instead loses the ones it did say in another shape -- a list of bullet
    points is still an explanation, and the drill-down used to show it.

    So the line is emptiness, not type. Anything falsy carries no words --
    ``None``, ``""``, ``[]``, ``{}``, ``false``, ``0`` -- and so does a string
    that is only whitespace, which is not falsy and is emptied by the strip
    rather than by the guard. Everything else renders as itself, WHOLE: this is
    the judge's own explanation and the only copy that survives, so it is not
    the place to save column width.
    """
    if not value:
        return ""
    return value.strip() if isinstance(value, str) else str(value)


def criterion_score(payload: object) -> tuple[float, str] | None:
    """Return a criterion's (score, justification), or None if it was not scored.

    Guards both levels. The criterion object can be absent or the wrong type,
    and it can be present with no usable score -- ``{}``, a justification with
    no ``score`` key, ``{"score": null}``, ``{"score": "n/a"}``. Every one of
    those used to coerce to 0.0 and become indistinguishable from a judge that
    read the answer and scored it zero.

    Reached by a WELL-FORMED reply that is incomplete, never by a truncated
    one: ``extract_json`` decodes with ``raw_decode``, so a cut-off reply
    raises there and the grader's ``except Exception`` turns it into a 0.0.
    **That 0.0 is the same defect on the path this function cannot see**, still
    averaged into the composite like a real score -- an asymmetry to close, not
    a design. Tracked as B61 in the internal findings register, a private
    companion to this repo, so the id will not resolve from here.
    """
    if not isinstance(payload, dict) or "score" not in payload:
        return None
    justification = _as_words(payload.get("justification"))
    raw = payload["score"]
    # `True` is an int in Python and would score 1.0; a bool is not a score.
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # NaN clamps to the MAXIMUM. Every comparison against nan is False, so
    # `min(1.0, nan)` returns 1.0 and the clamp yields a perfect score rather
    # than propagating the nan -- and the stdlib decoder `extract_json` uses
    # accepts a bare `NaN` literal, so a judge emitting one scored full marks.
    if not math.isfinite(value):
        return None
    return max(0.0, min(1.0, value)), justification


def unscored_reason(payload: object) -> str:
    """Say WHY a criterion has no score, without claiming more than is true.

    "Missing" is right only when the judge never emitted the criterion. When it
    emitted one it could not score -- ``{"score": null}``, ``{"score": "n/a"}``,
    a justification with no score at all -- the judge usually explains itself,
    and that explanation is worth more to whoever opens the drill-down than a
    message contradicted by the reply sitting next to it.
    """
    if payload is None:
        return "Missing criterion in judge output"
    if not isinstance(payload, dict):
        # The judge emitted something for this criterion, just not the object
        # the prompt asked for -- a bare `0.8`, a string. Calling that missing
        # denies a value sitting in the reply the operator is looking at.
        return f"Criterion in judge output is not an object: {quote_judge_value(payload)}"
    explanation = _as_words(payload.get("justification"))
    if explanation:
        return f"Not scored by the judge: {explanation}"
    return "Criterion present in judge output but carries no usable score"
