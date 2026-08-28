"""Reading one criterion's score, and saying honestly why there isn't one."""

from __future__ import annotations

import pytest
from beacon_graders.graders.judge_score import criterion_score, unscored_reason


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"score": 0.5, "justification": "half"}, (0.5, "half")),
        ({"score": "0.5"}, (0.5, "")),
        ({"score": 2.0}, (1.0, "")),
        ({"score": -1.0}, (0.0, "")),
    ],
)
def test_a_usable_score_comes_back_with_its_justification(
    payload: dict[str, object], expected: tuple[float, str]
) -> None:
    assert criterion_score(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not an object",
        3,
        {},
        {"justification": "no score"},
        {"score": None},
        {"score": "n/a"},
        {"score": float("nan")},
        {"score": True},
    ],
)
def test_anything_that_is_not_a_number_is_not_a_score(payload: object) -> None:
    assert criterion_score(payload) is None


def test_only_an_absent_criterion_is_called_missing() -> None:
    """A judge that emitted the wrong SHAPE still emitted something.

    Routing every non-dict to "Missing" told the drill-down a criterion was
    absent while its value sat in the reply -- `{"insight_recall": 0.8}` from a
    judge that ignored the prompted object shape.
    """
    assert unscored_reason(None) == "Missing criterion in judge output"
    assert "Missing" not in unscored_reason({"score": None})
    assert "Missing" not in unscored_reason(0.8)
    assert "0.8" in unscored_reason(0.8)
    assert "Missing" not in unscored_reason("0.8")


def test_a_non_string_justification_is_not_quoted_on_the_scored_path_either() -> None:
    """The same `str()` coercion lived one function up, on a scored criterion."""
    assert criterion_score({"score": 0.5, "justification": None}) == (0.5, "")


def test_the_judges_own_words_are_carried_through() -> None:
    assert unscored_reason({"justification": "  no evidence  "}) == (
        "Not scored by the judge: no evidence"
    )


@pytest.mark.parametrize("justification", [None, 0, [], {"a": 1}])
def test_a_non_string_justification_is_not_quoted_as_an_explanation(
    justification: object,
) -> None:
    """`str(None)` is the truthy literal "None", which would be quoted as one."""
    reason = unscored_reason({"score": None, "justification": justification})

    assert reason == "Criterion present in judge output but carries no usable score"
