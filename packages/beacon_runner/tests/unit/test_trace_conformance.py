"""What a run claims, against what its trace shows.

A runner executing off platform declares its own arm. If the declaration and
the trace disagree, the attribution engine compares two configurations that are
not what they say they are and attributes the difference to a layer.
"""

from __future__ import annotations

from typing import Literal

from beacon_runner.trace_conformance import layer_contradictions, observed_layers
from beacon_runner.types import ExecutionStep

_Status = Literal["COMPLETED", "FAILED", "SKIPPED", "RUNNING"]


def _layer(name: str, status: _Status) -> ExecutionStep:
    return ExecutionStep(uuid=f"u-{name}", name=name, level=f"layer:{name}", status=status)


def _trace(*layers: ExecutionStep) -> ExecutionStep:
    return ExecutionStep(
        uuid="root", name="run", level="workflow", status="COMPLETED", children=list(layers)
    )


def test_an_agreeing_trace_has_no_contradictions() -> None:
    trace = _trace(_layer("verifier", "COMPLETED"), _layer("self_consistency", "SKIPPED"))

    assert layer_contradictions(trace, {"verifier": True, "self_consistency": False}) == []


def test_a_layer_declared_on_but_skipped_is_a_contradiction() -> None:
    trace = _trace(_layer("verifier", "SKIPPED"))

    found = layer_contradictions(trace, {"verifier": True})

    assert len(found) == 1
    assert "declared enabled" in found[0]


def test_a_layer_declared_off_but_run_is_a_contradiction() -> None:
    """The dangerous direction: an ablated arm that did not actually ablate."""
    trace = _trace(_layer("self_consistency", "COMPLETED"))

    found = layer_contradictions(trace, {"self_consistency": False})

    assert len(found) == 1
    assert "declared disabled" in found[0]


def test_a_failed_layer_still_counts_as_having_run() -> None:
    """FAILED means it executed, which contradicts a claim it was switched off."""
    trace = _trace(_layer("verifier", "FAILED"))

    assert layer_contradictions(trace, {"verifier": False}) != []
    assert layer_contradictions(trace, {"verifier": True}) == []


def test_a_trace_with_no_layer_steps_says_nothing() -> None:
    """Silence is not disagreement; refusing it would reject uninstrumented runners."""
    trace = _trace()

    assert layer_contradictions(trace, {"verifier": True}) == []


def test_a_layer_the_config_does_not_mention_is_not_judged() -> None:
    trace = _trace(_layer("mode_routing", "COMPLETED"))

    assert layer_contradictions(trace, {"verifier": True}) == []


def test_layers_are_found_at_any_depth() -> None:
    """Instrumentation nests; a contradiction must not hide under a wrapper step."""
    nested = ExecutionStep(
        uuid="mid",
        name="pipeline",
        level="stage",
        status="COMPLETED",
        children=[_layer("verifier", "SKIPPED")],
    )
    trace = _trace(nested)

    assert layer_contradictions(trace, {"verifier": True}) != []


def test_a_layer_that_appears_twice_ran_if_any_occurrence_ran() -> None:
    trace = _trace(_layer("verifier", "SKIPPED"), _layer("verifier", "COMPLETED"))

    assert observed_layers(trace) == {"verifier": True}
    assert layer_contradictions(trace, {"verifier": True}) == []


def test_every_contradiction_is_reported_not_just_the_first() -> None:
    trace = _trace(_layer("verifier", "SKIPPED"), _layer("grounding", "COMPLETED"))

    found = layer_contradictions(trace, {"verifier": True, "grounding": False})

    assert len(found) == 2
