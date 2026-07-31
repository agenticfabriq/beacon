from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def test_adapter_registered() -> None:
    from beacon_benchmarks import ADAPTERS

    assert "text2vis" in ADAPTERS
    assert ADAPTERS["text2vis"].metadata.suite == "text2vis_v1"


def test_preprocess_emits_drafts_with_data_table_and_answer(tmp_path: Path) -> None:
    from beacon_benchmarks.text2vis.adapter import Text2VisAdapter

    root = tmp_path / "text2vis"
    data_root = root / "Text2Vis" / "data"
    data_root.mkdir(parents=True)
    (data_root / "001.json").write_text(
        json.dumps(
            {
                "qid": "001",
                "question": "Which country has the highest poverty share?",
                "data_table_csv": "country,share\nNigeria,43.54\nIndia,0.76\n",
                "ground_truth": {
                    "answer": "Nigeria",
                    "data_table_csv": "country,share\nNigeria,43.54\nIndia,0.76\n",
                    "chart_type": "bar",
                },
            }
        ),
        encoding="utf-8",
    )

    drafts = Text2VisAdapter().preprocess(root)

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.ground_truth["answer"] == "Nigeria"
    assert draft.ground_truth["chart_type"] == "bar"
    assert "country" in draft.context["data_table_csv"]


def test_register_graders_binds_data_grounded_and_answer_match() -> None:
    class Registry:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def register(self, grader: Any) -> None:
            self.added.append(grader)

    from beacon_benchmarks.text2vis.adapter import Text2VisAdapter

    registry = Registry()
    graders = Text2VisAdapter().register_graders(registry)

    names = {grader.name for grader in graders}
    assert "text2vis.data_grounded" in names
    assert "text2vis.factoid.answer" in names
    assert registry.added == graders
