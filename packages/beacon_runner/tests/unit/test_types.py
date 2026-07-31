from __future__ import annotations

from uuid import uuid4

import pydantic
import pytest
from beacon_runner.types import (
    EvalItem,
    ExecutionResult,
    ExecutionStep,
    Layer,
    SolutionConfig,
    SolutionIdentity,
)


def test_solution_identity_supports_empty_layers() -> None:
    sid = SolutionIdentity(
        solution_id="dummy",
        version="0.2",
        owner_team=uuid4(),
        summary="x",
        supported_modes=["EVAL"],
        layers=[],
    )
    assert sid.layers == []
    assert "EVAL" in sid.supported_modes


def test_layer_accepts_synthetic() -> None:
    layer = Layer(
        name="ontology",
        description="d",
        ablation_semantic="when off, drops 14 pts",
        instrumentation="synthetic",
    )
    assert layer.instrumentation == "synthetic"


def test_layer_validates_instrumentation_enum() -> None:
    with pytest.raises(pydantic.ValidationError):
        Layer.model_validate(
            {
                "name": "x",
                "description": "d",
                "ablation_semantic": "s",
                "instrumentation": "bogus",
            }
        )


def test_execution_step_supports_nested_children() -> None:
    leaf = ExecutionStep(
        uuid="c1",
        name="x",
        level="layer:ontology",
        status="COMPLETED",
    )
    root = ExecutionStep(
        uuid="r",
        name="root",
        level="workflow",
        status="COMPLETED",
        children=[leaf],
    )
    assert root.children[0].uuid == "c1"


def test_execution_step_to_dict_roundtrip() -> None:
    step = ExecutionStep(
        uuid="r",
        name="root",
        level="workflow",
        status="COMPLETED",
        inputs={"q": "?"},
        outputs={"a": "!"},
        children=[ExecutionStep(uuid="c", name="c", level="step", status="COMPLETED")],
    )
    data = step.to_dict()
    assert data["name"] == "root"
    assert data["children"][0]["name"] == "c"
    assert ExecutionStep.from_dict(data).uuid == "r"


def test_eval_item_minimal_construction() -> None:
    item = EvalItem(
        item_id="i-1",
        suite="dummy_smoke_v1",
        query={"question": "what is 2+2?"},
        ground_truth={"answer": "4"},
        metadata={},
    )
    assert item.item_id == "i-1"


def test_execution_result_defaults() -> None:
    result = ExecutionResult(
        output={"answer": "4"},
        output_kind="answer",
        trace=ExecutionStep(uuid="r", name="root", level="workflow", status="COMPLETED"),
    )
    assert result.tokens_input == 0
    assert result.error is None


def test_solution_config_layers_enabled_default_true() -> None:
    config = SolutionConfig(model_id="dummy", prompt_version="v0", layers_enabled={})
    assert config.is_layer_enabled("anything") is True
    configured = SolutionConfig(
        model_id="dummy",
        prompt_version="v0",
        layers_enabled={"ontology": False},
    )
    assert configured.is_layer_enabled("ontology") is False
    assert configured.is_layer_enabled("retry_loop") is True
