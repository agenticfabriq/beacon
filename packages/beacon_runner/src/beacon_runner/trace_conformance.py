"""Check what a run says it did against what its trace shows it did.

``config.layers_enabled`` is what the caller *claims*; a trace records what
actually happened, as steps at level ``layer:<name>`` carrying COMPLETED or
SKIPPED. Both are persisted for every result and nothing compared them.

Under tracker mode this stops being hypothetical. The runner executes off
platform and declares its own arm, so a mislabelled or misconfigured arm makes
the attribution engine compare two identical configurations and attribute the
difference to a layer. The evidence to catch it is already in the payload.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from beacon_runner.types import ExecutionStep

_LAYER_PREFIX = "layer:"
# A layer that ran, whether or not it succeeded. FAILED still means it executed,
# which contradicts a claim that it was switched off.
_RAN = frozenset({"COMPLETED", "FAILED", "RUNNING"})


def _walk(step: ExecutionStep) -> Iterator[ExecutionStep]:
    yield step
    for child in step.children:
        yield from _walk(child)


def observed_layers(step: ExecutionStep) -> dict[str, bool]:
    """Return each layer the trace mentions and whether it ran."""
    observed: dict[str, bool] = {}
    for node in _walk(step):
        level = node.level or ""
        if not level.startswith(_LAYER_PREFIX):
            continue
        name = level[len(_LAYER_PREFIX) :]
        if not name:
            continue
        # A layer appearing more than once ran if any occurrence ran.
        observed[name] = observed.get(name, False) or node.status in _RAN
    return observed


def layer_contradictions(step: ExecutionStep, layers_enabled: Mapping[str, bool]) -> list[str]:
    """Return every layer whose declared state the trace contradicts.

    Only layers the trace actually mentions are checked. A trace that carries no
    layer steps says nothing, and treating silence as disagreement would refuse
    every runner that does not instrument at this granularity.
    """
    contradictions: list[str] = []
    for name, ran in observed_layers(step).items():
        if name not in layers_enabled:
            continue
        declared = bool(layers_enabled[name])
        if declared and not ran:
            contradictions.append(f"{name}: declared enabled, trace shows it was skipped")
        elif not declared and ran:
            contradictions.append(f"{name}: declared disabled, trace shows it ran")
    return sorted(contradictions)
