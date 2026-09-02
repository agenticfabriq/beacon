"""One constant decides which outcomes a matrix rate counts.

B68 was a numerator counting results its denominator had thrown out: a verdict
survives an ERROR composite, so a PASS/PASS/ERROR row with a true reading on
all three yielded 3/2, which the UI rendered as 150%. The fix restricted every
gate to `_GRADED`.

The behavioural tests cover the six gates that exist. Nothing covered the
invariant -- that a SEVENTH gate, added later with a literal tuple, would be a
second guard agreeing with the first only by coincidence. That is this file.

It is deliberately narrow. "No literal outcome strings in matrix.py" would be
the obvious rule and the wrong one: the file has six legitimate per-outcome
comparisons (`Result.outcome == "PASS"`, `== "FAIL"`, `== "DEFER"`) that are
numerators for individual outcomes, not gates. A guard that fires on those
reads as noise and gets deleted. The true property is narrower -- every
set-membership test on `outcome` names the constant -- and it is the one worth
asserting.

**Reach, stated rather than left to be inferred.** This reads the source with
`ast`, so it sees `<anything>.outcome.in_(X)` regardless of what the left-hand
name is aliased to. It does NOT see a gate built dynamically: a column held in
a variable, `getattr(Result, "outcome")`, or a filter assembled from a list
comprehension are all invisible to it. It covers `routes/matrix.py` alone,
which is where every such gate currently lives -- a rate computed in a new
module is out of scope until someone adds it here.
"""

from __future__ import annotations

import ast
from pathlib import Path

MATRIX = Path(__file__).resolve().parents[2] / "src" / "beacon_ui" / "api" / "routes" / "matrix.py"

GATE_CONSTANT = "_GRADED"


def _outcome_membership_gates(tree: ast.AST) -> list[tuple[int, str]]:
    """Every `<...>.outcome.in_(ARG)` call, as (line, source of ARG)."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "in_":
            continue
        inner = func.value
        if not isinstance(inner, ast.Attribute) or inner.attr != "outcome":
            continue
        arg = ast.unparse(node.args[0]) if node.args else "<no argument>"
        found.append((node.lineno, arg))
    return found


def test_every_outcome_membership_gate_names_the_constant() -> None:
    source = MATRIX.read_text(encoding="utf-8")
    gates = _outcome_membership_gates(ast.parse(source))

    assert gates, (
        "No `outcome.in_(...)` gate found in matrix.py. Either the rates stopped "
        "filtering by outcome -- in which case B68 is back -- or they were "
        "rewritten in a form this guard cannot see. Read the file; do not "
        "delete this test."
    )

    literal = [(line, arg) for line, arg in gates if arg != GATE_CONSTANT]
    assert not literal, (
        f"These gates do not name {GATE_CONSTANT}: {literal}. A rate whose "
        f"denominator and numerator are gated separately agrees with itself "
        f"only by coincidence, which is B68. Use the constant."
    )


def test_the_constant_is_the_one_the_gates_name() -> None:
    """A guard over a constant that no longer exists would pass on zero gates."""
    source = MATRIX.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assigned = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert GATE_CONSTANT in assigned, (
        f"{GATE_CONSTANT} is not assigned in matrix.py, so the gate check above "
        f"is asserting against a name that does not exist."
    )
