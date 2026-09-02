"""Every numerator over the graded denominator is restricted to graded rows.

B68 was a numerator counting results its denominator had thrown out: a verdict
survives an ERROR composite, so a PASS/PASS/ERROR row with a true reading on
all three yielded 3/2, which the UI rendered as 150%. The fix restricted the
metric aggregates to `_GRADED`.

The behavioural tests cover the aggregates that exist today. This covers the
invariant, and it DERIVES what to check rather than listing it: the numerators
are exactly the `_rate(int(record.X), graded)` call sites, so an aggregate
added tomorrow and divided by `graded` is checked the moment it is divided. An
earlier version hand-listed five names, which left a sixth added later
invisible -- the same shape as B68 itself.

Two ways to be restricted, and both are correct:

* `outcome.in_(_GRADED)` -- set membership naming the constant, which the
  metric aggregates use.
* `outcome == "PASS"` -- already narrowed to a single outcome that is itself in
  `_GRADED`, which `n_pass`, `n_fail` and `n_defer` use. Gating those by the
  constant as well would be redundant, not safer.

`notin_` is rejected even when it names the constant.
`outcome.notin_(_GRADED)` reads like a gate, satisfies any check that inspects
only the argument, and selects exactly the rows the denominator excludes -- a
worse B68 than the original. The complement is the natural mistake here,
because matrix.py's module docstring states the rule as an exclusion ("ERROR
leaves the denominator"). If one is ever genuinely wanted, this test is the
place to argue for it.

**Reach, stated rather than left to be inferred.** This reads source with
`ast`. It sees `<anything>.outcome.in_/.notin_(X)` whatever the left-hand name
is aliased to, and equality against a string literal. It does NOT see a gate
built dynamically -- a column in a variable, `getattr(Result, "outcome")`, a
filter assembled in a comprehension -- nor one lifted into a helper, since it
walks only the expression the `.label()` hangs off. It covers
`routes/matrix.py` alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

MATRIX = Path(__file__).resolve().parents[2] / "src" / "beacon_ui" / "api" / "routes" / "matrix.py"

GATE_CONSTANT = "_GRADED"
RATE_FUNC = "_rate"
DENOMINATOR = "graded"


def _tree() -> ast.Module:
    return ast.parse(MATRIX.read_text(encoding="utf-8"))


def _graded_outcomes(tree: ast.Module) -> set[str]:
    """The literal outcomes `_GRADED` is bound to."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if GATE_CONSTANT not in names or not isinstance(node.value, ast.Tuple):
            continue
        return {
            e.value
            for e in node.value.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        }
    return set()


def _numerators_over_graded(tree: ast.Module) -> set[str]:
    """Labels X in every `_rate(int(record.X), graded)` -- the numerators."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != RATE_FUNC or len(node.args) != 2:
            continue
        second = node.args[1]
        if not isinstance(second, ast.Name) or second.id != DENOMINATOR:
            continue
        for inner in ast.walk(node.args[0]):
            if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name):
                found.add(inner.attr)
    return found


def _restriction_of(expr: ast.AST) -> str | None:
    """How this expression restricts `outcome`, tagged, or None if it does not."""
    for node in ast.walk(expr):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func = node.func
            inner = func.value
            if isinstance(inner, ast.Attribute) and inner.attr == "outcome":
                if func.attr == "notin_":
                    return "notin_"
                if func.attr == "in_":
                    return "in_:" + (ast.unparse(node.args[0]) if node.args else "<none>")
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = node.left, node.comparators[0]
            if (
                isinstance(node.ops[0], ast.Eq)
                and isinstance(left, ast.Attribute)
                and left.attr == "outcome"
                and isinstance(right, ast.Constant)
                and isinstance(right.value, str)
            ):
                return "eq:" + right.value
    return None


def _labelled_aggregates(tree: ast.Module) -> dict[str, ast.AST]:
    """Each `.label("n_x")` mapped to the expression it hangs off."""
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "label" or not node.args:
            continue
        name = node.args[0]
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            out[name.value] = node.func.value
    return out


def test_every_numerator_over_graded_is_restricted_to_graded_rows() -> None:
    tree = _tree()
    graded = _graded_outcomes(tree)
    numerators = _numerators_over_graded(tree)
    aggregates = _labelled_aggregates(tree)

    assert graded, f"{GATE_CONSTANT} is not bound to a tuple of string literals in matrix.py."
    assert numerators, (
        f"No `{RATE_FUNC}(int(record.X), {DENOMINATOR})` call found. The rates were "
        f"restructured; this guard is now checking nothing. Read the file."
    )

    problems: list[str] = []
    for name in sorted(numerators):
        expr = aggregates.get(name)
        if expr is None:
            problems.append(f"{name}: divided by {DENOMINATOR} but no `.label({name!r})` found")
            continue
        form = _restriction_of(expr)
        if form is None:
            problems.append(f"{name}: no outcome restriction -- this is B68")
        elif form == "notin_":
            problems.append(f"{name}: uses notin_, which selects the excluded rows")
        elif form.startswith("in_:") and form[4:] != GATE_CONSTANT:
            problems.append(f"{name}: gated on {form[4:]!r} rather than {GATE_CONSTANT}")
        elif form.startswith("eq:") and form[3:] not in graded:
            problems.append(f"{name}: narrowed to {form[3:]!r}, which is not in {GATE_CONSTANT}")

    assert not problems, (
        "Numerators divided by the graded denominator must count only graded rows:\n  "
        + "\n  ".join(problems)
        + f"\n\nUse `outcome.in_({GATE_CONSTANT})`, or equality against one outcome that is in "
        f"it. If a gate was lifted into a helper, this guard reads syntax and cannot follow it "
        f"-- say so here rather than deleting the test."
    )


def test_the_constant_is_bound_in_the_module() -> None:
    """A guard over an unbound constant would pass on zero gates."""
    tree = _tree()

    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            bound |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            # Moving the constant to a shared module is fine; still bound here.
            bound |= {a.asname or a.name for a in node.names}

    assert GATE_CONSTANT in bound, (
        f"{GATE_CONSTANT} is neither assigned nor imported in matrix.py, so the check "
        f"above is asserting against a name that is not bound there."
    )
