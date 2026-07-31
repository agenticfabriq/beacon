from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def test_adapter_registered() -> None:
    from beacon_benchmarks import ADAPTERS

    assert "dabstep" in ADAPTERS


def test_preprocess_emits_drafts_with_guidelines(tmp_path: Path) -> None:
    from beacon_benchmarks.dabstep.adapter import DabstepAdapter

    root = tmp_path / "dabstep"
    root.mkdir()
    tasks = [
        {
            "task_id": "1753",
            "question": "What are the applicable fee IDs?",
            "answer": "384, 394, 276",
            "guidelines": "list comma separated",
            "level": "hard",
        },
        {
            "task_id": "1754",
            "question": "Total revenue?",
            "answer": "12345.67",
            "guidelines": "single number",
            "level": "easy",
        },
        {
            "task_id": "1755",
            "question": "Refunds in Q5?",
            "answer": "Not Applicable",
            "guidelines": "single number or Not Applicable",
            "level": "easy",
        },
    ]
    with (root / "tasks.jsonl").open("w") as file:
        for task in tasks:
            file.write(json.dumps(task) + "\n")
    (root / "context").mkdir()
    (root / "context" / "manual.md").write_text("# Adyen manual\n")
    (root / "context" / "payments.csv").write_text("id,amount\n1,100\n")

    drafts = DabstepAdapter().preprocess(root)

    assert len(drafts) == 3
    by_id = {draft.metadata["task_id"]: draft for draft in drafts}
    assert by_id["1753"].ground_truth["answer"] == "384, 394, 276"
    assert by_id["1753"].query["guidelines"].startswith("list")
    assert by_id["1755"].ground_truth["answer"] == "Not Applicable"
    assert "manual.md" in by_id["1753"].context["files"]
    assert "payments.csv" in by_id["1753"].context["files"]
