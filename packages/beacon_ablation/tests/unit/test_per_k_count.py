"""`per_k_count` answers "how many", or says it does not know."""

from __future__ import annotations

import pytest
from beacon_ablation import per_k_count


@pytest.mark.parametrize(
    ("entry", "expected", "why"),
    [
        ({"n_compared": 5}, 5, "a count"),
        ({"n_compared": 0}, 0, "zero is a real answer: nothing was compared"),
        ({"delta": 0.1}, None, "the key is absent"),
        ({}, None, "an empty entry"),
        (None, None, "no entry at all"),
        ("not a mapping", None, "not a mapping"),
        # A float is refused rather than truncated. `n_compared` is a
        # cardinality; 4.7 of anything means the writer had something else.
        ({"n_compared": 5.0}, None, "a float is not a count"),
    ],
)
def test_what_a_count_reads_as(entry: object, expected: int | None, why: str) -> None:
    assert per_k_count(entry, "n_compared") is expected, why


@pytest.mark.parametrize("value", [True, False])
def test_a_bool_is_not_a_sample_size(value: bool) -> None:
    """`isinstance(True, int)` is True, so a bool needs refusing explicitly.

    Left in, `True` reads as a sample of one and `False` as "nothing was
    compared" -- both indistinguishable from a real measurement. This is the
    one property of the function that an `== 5` assertion elsewhere cannot
    check, because `5` already excludes both.
    """
    assert per_k_count({"n_compared": value}, "n_compared") is None
