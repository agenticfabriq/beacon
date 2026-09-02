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
  `_GRADED`, which `n_pass`, `n_fail`, `n_defer` and `n_pass_target_engine`
  use. Gating those by the constant as well would be redundant, not safer.

`notin_` is rejected even when it names the constant.
`outcome.notin_(_GRADED)` reads like a gate, satisfies any check that inspects
only the argument, and selects exactly the rows the denominator excludes -- a
worse B68 than the original. The complement is the natural mistake here,
because matrix.py's module docstring states the rule as an exclusion ("ERROR
leaves the denominator"). If one is ever genuinely wanted, this test is the
place to argue for it.

**Three checks, because no two of them cover the third.**

`test_every_membership_gate_names_the_constant` is module-wide: any
`outcome.in_(...)` anywhere must name `_GRADED`. It catches a gate that
disagrees with the constant. It cannot catch a gate that is absent, because
there is then nothing to inspect.

`test_every_numerator_over_graded_is_restricted_to_graded_rows` derives the
numerators from the `_rate(int(record.X), graded)` call sites, so an aggregate
added tomorrow is checked the moment it is divided. It catches a numerator with
NO restriction. It never reaches `_per_run_rate`, whose result is labelled
"rate" and divided by nothing.

`test_the_per_run_denominator_still_has_its_gate` asserts presence and exact
shape at one named site, because the other two are both blind there: removing
that gate leaves nothing for the first to inspect, and the second does not look.

An earlier version had only the first, then only the second, then the first two
without the third. Each time the missing one was the sole cover for a real
form, and each gap was found by mutation rather than by reading.

**Reach, stated rather than left to be inferred.** All three read source with
`ast`, and all three cover `routes/matrix.py` alone.

The first two see `<anything>.outcome.in_/.notin_(X)` whatever the left-hand
name is aliased to. Equality against a string literal is read by the derived
check, and exactly by the per-run one. The module-wide walk skips any node
whose attribute is not `in_` or `notin_` and never inspects a comparison, so an
`outcome == "..."` that is neither a numerator over `graded` nor inside
`_per_run_rate`'s divisor is seen by nothing here.

The derived check collects EVERY restriction in an aggregate rather than the
first, so a condition OR-ing an excluded outcome beside a graded one fails on
the excluded half, and a membership test wrapped in `~` or `sa.not_` is tagged
as the complement it is.

Negation is handled on membership gates and equalities in four spellings: `~`,
`sa.not_(...)`, bare `not_(...)`, and `.is_(False)` / `== False`. Complement
spellings are open-ended and this list is not closed -- `.is_not(True)`,
`.isnot(True)`, `!= True`, `.is_(sa.false())` and `False == <gate>` all still
read as the gate itself. `.isnot(...)` is already live in matrix.py, so that
is a spelling someone reaches for, not a hypothetical. Adding one is a line in
`_negated_nodes`; the reason they are not all there is that each was added when
a mutation found it, and these have not been.

**One known blind spot, left open deliberately.**

It records WHICH restrictions an aggregate contains, not how they are combined,
so an OR whose other half is not an outcome predicate at all --
`sa.or_(outcome == "PASS", output[...].isnot(None))` -- is seen as `eq:PASS`
and passes, while admitting every ERROR row satisfying the second disjunct. A
negated NON-gate predicate in that position is the same gap by the same
mechanism, not a second one: the surviving acceptable tag satisfies the check
on its own either way.

Closing it means evaluating arbitrary SQLAlchemy boolean structure, which is a
query planner inside a unit test -- and that reasoning does NOT extend to
anything syntactic. It was offered once for `.is_(False)`, wrongly: that turned
out to be five lines beside the `not_` branch, so the justification for
stopping was doing work the code should have done. The behavioural tests cover
today's aggregates; this is the form a NEW one could take without this file
noticing, written down rather than left to be rediscovered one mutation at a
time.

The per-run check, by contrast, is NOT alias-independent: it compares the
unparsed condition against the literal string `Result.outcome.in_(_GRADED)`, so
renaming the model import to `res`
fails it. That is deliberate -- it is pinning one known site exactly, and a
behaviour-preserving rename there should be a decision someone makes on
purpose rather than one this test waves through.

None of them sees a gate built dynamically: a column in a variable,
`getattr(Result, "outcome")`, or a filter assembled in a comprehension. The
derived check additionally cannot follow a gate lifted into a helper, since it
walks only the expression the `.label()` hangs off; the module-wide one still
would.
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


def _negated_nodes(expr: ast.AST) -> set[int]:
    """ids of every node under a `~` or a `not_(...)`.

    `ast.walk` descends into both, so without this `~outcome.in_(_GRADED)` is
    indistinguishable from the gate itself -- naming the constant while
    selecting exactly the rows it excludes.

    Four spellings: `~`, `sa.not_(...)`, a bare `not_(...)` under
    `from sqlalchemy import not_`, and `<expr>.is_(False)` / `<expr> == False`,
    which compile to the same complement. All syntactic, so all detectable
    here without evaluating anything.
    """
    negated: set[int] = set()
    for node in ast.walk(expr):
        operand: ast.AST | None = None
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
            operand = node.operand
        elif (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.Eq)
            and isinstance(node.comparators[0], ast.Constant)
            and node.comparators[0].value is False
        ):
            operand = node.left
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else None
            )
            if name == "not_":
                operand = node.args[0]
            elif (
                name == "is_"
                and isinstance(func, ast.Attribute)
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value is False
            ):
                # `outcome.in_(_GRADED).is_(False)` is the same complement as
                # `~`, and purely syntactic -- detectable without evaluating.
                operand = func.value
        if operand is not None:
            negated.update(id(child) for child in ast.walk(operand))
    return negated


def _restrictions_of(expr: ast.AST) -> list[str]:
    """EVERY way this expression restricts `outcome`, tagged.

    All of them, not the first. Returning the first lets a numerator OR-ed with
    an excluded outcome -- `sa.or_(outcome == "PASS", outcome == "ERROR")` --
    read as `eq:PASS` and pass, which puts ERROR rows into `n_pass` while the
    denominator still excludes them: `ex_rate` over 100%, B68 verbatim.
    """
    found: list[str] = []
    negated = _negated_nodes(expr)

    for node in ast.walk(expr):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func = node.func
            inner = func.value
            if isinstance(inner, ast.Attribute) and inner.attr == "outcome":
                if func.attr in ("in_", "notin_") and id(node) in negated:
                    found.append("negated:" + func.attr)
                elif func.attr == "notin_":
                    found.append("notin_")
                elif func.attr == "in_":
                    found.append("in_:" + (ast.unparse(node.args[0]) if node.args else "<none>"))
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = node.left, node.comparators[0]
            if (
                isinstance(node.ops[0], ast.Eq)
                and isinstance(left, ast.Attribute)
                and left.attr == "outcome"
                and isinstance(right, ast.Constant)
                and isinstance(right.value, str)
            ):
                # Negation matters here as much as on a membership gate:
                # `~(outcome == "PASS")` counts FAIL, DEFER and ERROR against a
                # denominator of PASS+FAIL+DEFER. That exceeds 100% only when
                # ERROR outnumbers PASS; below it the rate is silently wrong,
                # which is the harder case to notice.
                tag = "negated:eq" if id(node) in negated else "eq:"
                found.append(tag + right.value if tag == "eq:" else tag)
    return found


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
        forms = _restrictions_of(expr)
        if not forms:
            problems.append(f"{name}: no outcome restriction -- this is B68")
        # EVERY restriction must be acceptable, not just one. An aggregate
        # OR-ing an excluded outcome beside a graded one carries both, and
        # checking only the first waves it through.
        for form in forms:
            if form == "notin_" or form.startswith("negated:"):
                problems.append(
                    f"{name}: {form} -- a complement selects the rows the denominator "
                    f"excludes, whatever it names"
                )
            elif form.startswith("in_:") and form[4:] != GATE_CONSTANT:
                problems.append(f"{name}: gated on {form[4:]!r} rather than {GATE_CONSTANT}")
            elif form.startswith("eq:") and form[3:] not in graded:
                problems.append(f"{name}: admits {form[3:]!r}, not in {GATE_CONSTANT}")

    assert not problems, (
        "Numerators divided by the graded denominator must count only graded rows:\n  "
        + "\n  ".join(problems)
        + f"\n\nUse `outcome.in_({GATE_CONSTANT})`, or equality against one outcome that is in "
        f"it. If a gate was lifted into a helper, this guard reads syntax and cannot follow it "
        f"-- say so here rather than deleting the test."
    )


def test_every_membership_gate_names_the_constant() -> None:
    """Module-wide, and the only cover for gates that are not numerators.

    `_per_run_rate` builds its own denominator with `outcome.in_(_GRADED)` and
    labels the result "rate", so the derived check never reaches it. Swapping
    that for a literal tuple is numerically identical today and is precisely
    the second-guard-agreeing-by-coincidence this file exists to prevent.
    """
    tree = _tree()

    offenders: list[tuple[int, str]] = []
    seen = 0
    negated = _negated_nodes(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        func = node.func
        if func.attr not in ("in_", "notin_"):
            continue
        inner = func.value
        if not isinstance(inner, ast.Attribute) or inner.attr != "outcome":
            continue
        seen += 1
        arg = ast.unparse(node.args[0]) if node.args else "<none>"
        # Negation-aware, because this is the SOLE cover for the `n_graded`
        # gate: it is not a `_rate(..., graded)` numerator and not inside
        # `_per_run_rate`, so `~outcome.in_(_GRADED)` there would leave the
        # denominator behind every rate counting ERROR rows only.
        if id(node) in negated:
            offenders.append((node.lineno, f"~{func.attr}({arg})"))
        elif func.attr == "notin_" or arg != GATE_CONSTANT:
            offenders.append((node.lineno, f"{func.attr}({arg})"))

    assert seen, (
        "No outcome membership gate found anywhere in matrix.py. Either the rates "
        "stopped filtering by outcome -- in which case B68 is back -- or they were "
        "rewritten in a form this walk cannot see. Either way this test is now "
        "checking nothing; read the file rather than trusting the green."
    )
    assert not offenders, (
        f"Outcome membership gates that do not name {GATE_CONSTANT}: {offenders}. "
        f"A gate agreeing with the constant by coincidence stops agreeing the day "
        f"the convention changes, and no behavioural test would notice."
    )


def test_the_per_run_denominator_still_has_its_gate() -> None:
    """Checking gates that EXIST cannot see one removed, and this is the one.

    `_per_run_rate` builds the denominator behind `ex_rate_min` and
    `ex_rate_max`. Rewriting its filter to `outcome != "ERROR"` -- or deleting
    it, which puts ERROR straight back into that denominator, B68 verbatim --
    leaves no `in_`/`notin_` node to inspect, so the module-wide check finds
    nothing wrong, and the derived check never reaches it because the
    expression is labelled "rate" rather than divided by `graded`. Both stay
    green on a broken metric. Hence a check that asserts presence, by name.
    """
    tree = _tree()

    func = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_per_run_rate"
        ),
        None,
    )
    assert func is not None, (
        "`_per_run_rate` is gone from matrix.py. If the per-run spread moved, move "
        "this check with it; if it was deleted, delete this test deliberately."
    )

    # The DENOMINATOR's filter specifically, and it must be the whole condition.
    #
    # "contains an in_(_GRADED) node somewhere" is not enough: widening it to
    # `in_(_GRADED) | (outcome == "ERROR")` still contains one, and puts ERROR
    # back in the denominator behind ex_rate_min/ex_rate_max -- B68 verbatim,
    # the mutation this test exists for. The behavioural spread test cannot see
    # it either, since model-a's seed is PASS/FAIL/DEFER with no ERROR.
    #
    # The denominator is the `nullif(count().filter(X), 0)` divisor; X is the
    # condition that must be exactly the gate.
    divisors = [
        node.args[0]
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "nullif"
        and node.args
    ]
    assert divisors, (
        "No `nullif(...)` divisor found in `_per_run_rate`. The rate was "
        "restructured; this check no longer knows where the denominator is."
    )

    # EVERY argument of every filter under the divisor, not just args[0].
    # SQLAlchemy ANDs multiple criteria, so `.filter(gate, Result.error.is_(None))`
    # renders `outcome IN (...) AND error IS NULL` -- a NARROWER denominator that
    # agrees with _GRADED only because today's seed sets `error` on ERROR rows
    # alone. Checking one argument would pass it.
    conditions: list[str] = []
    for divisor in divisors:
        for node in ast.walk(divisor):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "filter"
            ):
                conditions.extend(ast.unparse(a) for a in node.args)

    expected = f"Result.outcome.in_({GATE_CONSTANT})"
    assert conditions == [expected], (
        f"`_per_run_rate`'s denominator must be exactly `{expected}` and nothing "
        f"else. Found: {conditions}. That denominator feeds ex_rate_min and "
        f"ex_rate_max, and nothing else in this file checks it -- the module-wide "
        f"test inspects gates that exist, so one removed, reshaped, widened or "
        f"AND-ed with a second condition is invisible to it. If the extra condition "
        f"is deliberate, change `expected` here and say why."
    )


def test_the_negation_detector_actually_detects() -> None:
    """The complement branches have no cover from matrix.py itself.

    matrix.py contains no `.is_(False)` and no `== False` on an outcome
    predicate, so deleting either branch of `_negated_nodes` leaves every other
    test in this file green -- detection code with no test, in a file whose
    whole subject is checks that cannot fail. Synthetic sources instead of
    mutating the real one, since the point is the helper, not the module.
    """
    covered = [
        "count().filter(~Result.outcome.in_(_GRADED))",
        "count().filter(sa.not_(Result.outcome.in_(_GRADED)))",
        "count().filter(not_(Result.outcome.in_(_GRADED)))",
        "count().filter(Result.outcome.in_(_GRADED).is_(False))",
        "count().filter(Result.outcome.in_(_GRADED) == False)",
        "count().filter(~(Result.outcome == 'PASS'))",
        # Compound, so the subtree walk is exercised. Without it -- marking only
        # the operand node itself -- every case above still passes while a
        # complement wrapping a conjunction reads as the gate inside it.
        "count().filter(~sa.or_(Result.outcome.in_(_GRADED), Result.error.is_(None)))",
        "count().filter(sa.not_(sa.and_(Result.outcome == 'PASS', Result.id.isnot(None))))",
    ]
    for src in covered:
        tags = _restrictions_of(ast.parse(src))
        assert any(t.startswith("negated:") for t in tags), (
            f"{src!r} is a complement and was tagged {tags}. A complement read as "
            f"a gate is the defect this whole file exists to catch."
        )

    plain = _restrictions_of(ast.parse("count().filter(Result.outcome.in_(_GRADED))"))
    assert plain == ["in_:_GRADED"], (
        f"An unnegated gate must not be tagged as a complement; got {plain}. A "
        f"detector that flags everything is as useless as one that flags nothing."
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
