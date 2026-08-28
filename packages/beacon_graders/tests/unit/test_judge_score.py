"""Reading one criterion's score, and saying honestly why there isn't one."""

from __future__ import annotations

import pytest
from beacon_graders.graders.judge_score import (
    criterion_score,
    quote_judge_value,
    unscored_reason,
)


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


@pytest.mark.parametrize("empty", [None, "", "   ", [], {}, False, 0])
def test_an_empty_justification_is_not_quoted_as_an_explanation(empty: object) -> None:
    """Anything falsy carries no words, whatever its type.

    `str()` on several of these yields a truthy string -- "None", "[]", "{}",
    "False" -- which would be quoted back as something the judge said.
    Emptiness is the line, not type, so whitespace and `0` go the same way.
    """
    reason = unscored_reason({"score": None, "justification": empty})

    assert reason == "Criterion present in judge output but carries no usable score"


@pytest.mark.parametrize(
    ("justification", "expected"),
    [
        pytest.param(None, "", id="null"),
        pytest.param([], "", id="empty list"),
        pytest.param(["a", "b"], "['a', 'b']", id="bullet list"),
        pytest.param("  spaced  ", "spaced", id="string is stripped"),
    ],
)
def test_the_scored_path_renders_a_justification_the_same_way(
    justification: object, expected: str
) -> None:
    """The rule has to hold where a criterion WAS scored, too.

    This string goes straight into `Verdict.justification` for a scored
    criterion, and the same coercion bug lived here first.
    """
    scored = criterion_score({"score": 0.5, "justification": justification})

    assert scored == (0.5, expected)


@pytest.mark.parametrize(
    ("justification", "shown"),
    [
        pytest.param(["a", "b"], "['a', 'b']", id="bullet list"),
        pytest.param({"why": "no evidence"}, "no evidence", id="object"),
        pytest.param(3, "3", id="number"),
    ],
)
def test_an_explanation_in_another_shape_is_still_an_explanation(
    justification: object, shown: str
) -> None:
    """Dropping every non-string loses words the judge did say.

    A list of bullet points is an explanation, and the drill-down used to show
    it. Only an absence should render as nothing; a shape should render as
    itself.
    """
    reason = unscored_reason({"score": None, "justification": justification})

    assert shown in reason
    assert reason.startswith("Not scored by the judge:")


def test_a_short_judge_value_is_quoted_whole() -> None:
    assert quote_judge_value([1, 2, 3]) == "[1, 2, 3]"


def test_a_long_judge_value_says_it_was_cut() -> None:
    """A silent cut reads as a complete value the judge never sent.

    `f"{x!r:.120}"` truncates without a marker, so `{'score': 12345678` looks
    like exactly what arrived. The marker is the difference between showing
    less and showing something else.
    """
    quoted = quote_judge_value(["x" * 50] * 20)

    assert quoted.startswith("['xxx")
    assert "cut," in quoted
    assert len(quoted) < 200


def test_the_cut_reports_the_real_length() -> None:
    value = "y" * 500

    quoted = quote_judge_value(value)

    assert f"{len(repr(value))} chars" in quoted
