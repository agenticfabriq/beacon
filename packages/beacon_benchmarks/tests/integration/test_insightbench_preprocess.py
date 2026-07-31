from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def test_adapter_registered() -> None:
    from beacon_benchmarks import ADAPTERS

    assert "insightbench" in ADAPTERS


def test_preprocess_emits_drafts_with_summary_and_insights(tmp_path: Path) -> None:
    from beacon_benchmarks.insightbench.adapter import InsightBenchAdapter

    root = tmp_path / "insightbench"
    (root / "datasets" / "ds_001").mkdir(parents=True)
    (root / "datasets" / "ds_001" / "data.csv").write_text("a,b\n1,2\n")
    (root / "datasets" / "ds_001" / "instance.json").write_text(
        json.dumps(
            {
                "instance_id": "ds_001",
                "question": "What are the top 3 attrition factors?",
                "context": "ServiceNow HR data...",
                "ground_truth": {
                    "summary": "Key drivers are tenure and salary band.",
                    "insights": [
                        {"content": "Attrition rises sharply after 24 months tenure."},
                        {"content": "Compensation band C has 2x churn vs B."},
                    ],
                    "recommendations": "Review compensation bands.",
                },
                "category": "attrition_analysis",
            }
        )
    )

    drafts = InsightBenchAdapter().preprocess(root)

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.suite == "insightbench_v1"
    assert "summary" in draft.ground_truth
    assert len(draft.ground_truth["insights"]) == 2
    assert draft.metadata["instance_id"] == "ds_001"
    assert draft.metadata["category"] == "attrition_analysis"
    assert "data.csv" in draft.context["files"]


def test_register_graders_returns_rubric_and_freetext() -> None:
    from beacon_benchmarks.insightbench.adapter import InsightBenchAdapter

    class FakeRegistry:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def register(self, grader: Any) -> None:
            self.added.append(grader)

    registry = FakeRegistry()
    graders = InsightBenchAdapter().register_graders(registry)
    grader_names = {grader.name for grader in graders}

    assert "insightbench.rubric.summary" in grader_names
    assert "insightbench.freetext.insights" in grader_names
    assert registry.added == graders
