from __future__ import annotations

from uuid import uuid4

from beacon_runner.dummy_sut import DummySUT
from beacon_runner.sut import SolutionUnderTest
from beacon_runner.types import EvalItem, SolutionConfig


def _item(idx: int = 0) -> EvalItem:
    return EvalItem(
        item_id=f"item-{idx}",
        suite="dummy_smoke_v1",
        query={"question": "?"},
        ground_truth={"answer": "yes"},
        metadata={},
    )


def test_dummy_satisfies_protocol() -> None:
    assert isinstance(DummySUT(owner_team_id=uuid4()), SolutionUnderTest)


def test_layers_are_ontology_and_retry_loop() -> None:
    sut = DummySUT(owner_team_id=uuid4())
    names = sorted(layer.name for layer in sut.layers())
    assert names == ["ontology", "retry_loop"]
    assert all(layer.instrumentation == "synthetic" for layer in sut.layers())


def test_validate_config_rejects_unknown_layer() -> None:
    sut = DummySUT(owner_team_id=uuid4())
    errs = sut.validate_config(
        SolutionConfig(
            model_id="dummy",
            prompt_version="v0",
            layers_enabled={"ontology": True, "bogus": False},
        )
    )
    assert any("bogus" in err for err in errs)


def test_invoke_is_deterministic_for_same_seed() -> None:
    sut = DummySUT(owner_team_id=uuid4())
    cfg = SolutionConfig(model_id="dummy", prompt_version="v0", layers_enabled={})
    r1 = sut.invoke(_item(1), cfg)
    r2 = sut.invoke(_item(1), cfg)
    assert r1.output == r2.output
    assert r1.output_kind == "answer"


def test_invoke_trace_has_layer_tagged_steps() -> None:
    sut = DummySUT(owner_team_id=uuid4())
    cfg = SolutionConfig(model_id="dummy", prompt_version="v0", layers_enabled={})
    res = sut.invoke(_item(2), cfg)
    levels = {child.level for child in res.trace.children}
    assert "layer:ontology" in levels
    assert "layer:retry_loop" in levels


def test_disabled_layer_marks_step_as_skipped() -> None:
    sut = DummySUT(owner_team_id=uuid4())
    cfg = SolutionConfig(
        model_id="dummy",
        prompt_version="v0",
        layers_enabled={"ontology": False},
    )
    res = sut.invoke(_item(3), cfg)
    ont_step = next(child for child in res.trace.children if child.level == "layer:ontology")
    assert ont_step.status == "SKIPPED"


def test_pass_rate_drops_when_ontology_off() -> None:
    sut = DummySUT(owner_team_id=uuid4())
    cfg_on = SolutionConfig(model_id="dummy", prompt_version="v0", layers_enabled={})
    cfg_off = SolutionConfig(
        model_id="dummy",
        prompt_version="v0",
        layers_enabled={"ontology": False},
    )
    n = 200
    p_on = sum(bool(sut.invoke(_item(i), cfg_on).output["passed"]) for i in range(n)) / n
    p_off = sum(bool(sut.invoke(_item(i), cfg_off).output["passed"]) for i in range(n)) / n
    assert 0.62 <= p_on <= 0.82
    assert (p_on - p_off) >= 0.05
    assert (p_on - p_off) <= 0.25
