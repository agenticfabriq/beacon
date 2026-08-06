"""Field-caught regression sentinels, frozen as the real data that caught them.

Five Spider 2.0-lite cases, each of which exposed a grading defect or forced a
semantic decision during the beacon/mnemiq convergence (2026-08-06/07). The
unit tests around them cover the same rules with hand-built minimal cases;
these are the actual shapes from the field -- multi-gold alternates, real
condition_cols, typed CSV gold, a preview against a large gold -- run through
the grader exactly as the import path runs them.

If one of these fails, a decision recorded in docs/grading.md has changed.
That is allowed only on purpose, with the doc and both implementations moving
together -- never as a side effect.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from beacon_graders.graders.result_set_match import ResultSetMatchGrader
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "spider2_sentinels.json"
_CASES = json.loads(_FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["case_id"] for c in _CASES])
def test_sentinel_grades_as_agreed(case: dict[str, Any]) -> None:
    grader = ResultSetMatchGrader()
    item = EvalItem(
        item_id=case["case_id"],
        suite="spider2_lite_local_v1",
        query={},
        ground_truth=case["gold_answer"],
        metadata=case["metadata"],
    )
    result = ExecutionResult(
        output=case["output"],
        output_kind="sql",
        trace=ExecutionStep(uuid="sentinel", name="sentinel", level="workflow"),
        tokens_input=0,
        tokens_output=0,
        runtime_ms=1,
    )

    verdicts = grader.grade(item, result)
    by_metric = {(v.metric or grader.metric): v for v in verdicts}
    expect = case["expect"]

    assert bool(by_metric["exact_match"].bool_value) == expect["exact_match"], case["pins"]
    assert bool(by_metric["got_facts"].bool_value) == expect["got_facts"], case["pins"]
    if "mismatch_kind" in expect:
        mismatch = (by_metric["exact_match"].raw_output or {}).get("mismatch") or {}
        assert mismatch.get("kind") == expect["mismatch_kind"], case["pins"]
