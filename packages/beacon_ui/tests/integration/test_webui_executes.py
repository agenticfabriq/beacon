"""The page's JavaScript must actually RUN, not merely parse.

Every other guard on this file reads it as text. That leaves a whole class
invisible, and it shipped: ``SUITE_PANELS`` dereferenced ``matrixCols`` in its
initializer while the ``const`` was declared ninety lines below it. A ``const``
is not hoisted, so evaluation hit the temporal dead zone, threw a
``ReferenceError`` at that line, and aborted the entire script -- every
listener and the boot call sit below it, so the page would have rendered its
markup and then done nothing whatsoever. For every user, not only as-of users.

``node --check`` passed it, because the syntax was fine. All seventy of the
matrix, webui and history tests passed it, because none of them executes the
page. This does.

Deliberately a SMOKE test, not a UI test. It stubs enough of the browser for
the top-level statements to evaluate and asserts nothing threw. It clicks
nothing: the point is the class of failure where the file is syntactically
perfect and dead on arrival.

Skipped when node is unavailable rather than silently passing -- a guard that
quietly does nothing is the shape of the bug it was written for.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple, cast

import pytest

pytestmark = pytest.mark.integration

WEBUI = Path(__file__).parents[2] / "src" / "beacon_ui" / "webui" / "index.html"

# Enough of a browser for the top-level statements to run. The stub answers
# structurally -- querySelectorAll returns a list, getElementById an object
# carrying the properties the page assigns -- because the failure being caught
# is "nothing ran", and a stub that threw on the first unknown property would
# be indistinguishable from it.
STUB = """
const __els = new Map();
function __el(id) {
  if (!__els.has(id)) __els.set(id, {
    id, textContent: "", innerHTML: "", value: "", style: {}, dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, setAttribute() {},
    removeAttribute() {}, toggleAttribute() {}, closest() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    appendChild() {}, after() {}, scrollIntoView() {}, focus() {}, click() {},
    getAttribute() { return null; }, insertAdjacentHTML() {},
  });
  return __els.get(id);
}
// The real matrix header cells, injected from the markup, so `matrixCols()`
// can be checked against the table it claims to measure. Everything else
// returns [] -- this is the one selector whose answer is load-bearing.
const __HEADER = __HEADER_CELLS__;
globalThis.document = {
  getElementById: __el,
  querySelector: () => __el("q"),
  querySelectorAll: (selector) =>
    selector === "#v-matrix thead tr:last-child th"
      ? __HEADER.map((cell) => ({
          style: { display: cell.display },
          getAttribute: (name) => (name === "colspan" ? cell.colspan : null),
        }))
      : [],
  createElement: () => __el("created"),
  addEventListener() {},
  body: __el("body"),
  documentElement: __el("html"),
};
globalThis.window = {
  location: { hash: "", href: "http://localhost/ui", assign() {}, replace() {} },
  addEventListener() {}, removeEventListener() {},
  scrollTo() {}, history: { replaceState() {}, pushState() {} },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  setTimeout, clearTimeout, prompt: () => null, confirm: () => true, alert() {},
};
globalThis.localStorage = {
  _v: {}, getItem(k) { return this._v[k] ?? null; },
  setItem(k, v) { this._v[k] = String(v); }, removeItem(k) { delete this._v[k]; },
};
globalThis.sessionStorage = globalThis.localStorage;
globalThis.fetch = () => Promise.resolve({
  ok: true, status: 200, statusText: "OK",
  json: () => Promise.resolve({}), text: () => Promise.resolve(""),
});
// `navigator` is getter-only on modern node, so it is DEFINED rather than
// assigned -- assigning it throws, and that throw would masquerade as exactly
// the failure this test exists to detect.
Object.defineProperty(globalThis, "navigator", {
  value: { clipboard: { writeText: () => Promise.resolve() } },
  configurable: true,
});
globalThis.prompt = () => null;
globalThis.requestAnimationFrame = (fn) => fn();
"""

# Names at several depths of the file: a script that died at line 500 still
# defines everything above it, so one probe near the top would prove little.
_PROBED = (
    "matrixCols",
    "SUITE_PANELS",
    "ROUTED_VIEWS",
    "ENDPOINTS",
    "api",
    "moved",
    "deltaText",
    "loadMatrix",
    "renderMatrixRows",
    "loadHistory",
    "openHistoryEvent",
    "writeRoute",
    "go",
)

# Referenced DIRECTLY, not through `globalThis`. The probe is concatenated into
# the same module scope as the page's top-level declarations, and a module's
# `const` never becomes a global property -- reading `globalThis` reported every
# name missing on a script that had run perfectly well. `typeof` on an
# undeclared identifier is safe, so this separates "never declared" from
# "declared and undefined".
PROBE = (
    "const missing = [];\n"
    + "".join(f'if (typeof {name} === "undefined") missing.push("{name}");\n' for name in _PROBED)
    + """
// The declaration that shipped broken: a panel width given as the reader must
// RESOLVE to a function, not merely read like one in the source.
const panel = SUITE_PANELS.find(([id]) => id === "matrix-rows");
const declared = panel && panel[1];
// The three states of the delta cell, exercised rather than read. `affected`
// and `changed_since` disagree exactly when a LATER regrade moved a row the
// selected event left alone, and that row still has a real delta -- so the
// cell has to show it AND say whose it is.
const __delta = (affected, changed_since) => {
  const row = { affected, changed_since, ex_rate: 0.75, current: { ex_rate: 1.0 } };
  return { text: deltaText(row), cls: deltaClass(row), title: deltaTitle(row) };
};

console.log(JSON.stringify({
  missing,
  matrix_panel_is_reader: typeof declared === "function",
  matrix_panel_resolves: typeof declared === "function" ? declared() : declared,
  delta_this_event: __delta(true, true),
  delta_later_only: __delta(false, true),
  delta_untouched: __delta(false, false),
}));
"""
)


class _HeaderCell(NamedTuple):
    """One header cell, as the stub and the expectation both need it."""

    colspan: str | None
    display: str


def _matrix_header_cells() -> list[_HeaderCell]:
    """The matrix header's last row, as (colspan, display) per cell.

    Parsed from the markup so the JavaScript reader can be held against the
    table rather than against a number written down twice. ``display:none``
    matters because the delta column lives in the markup and is hidden until
    an as-of is in force.
    """
    html = WEBUI.read_text()
    start = html.index('id="matrix-rows"')
    table = html.rindex("<table", 0, start)
    thead = re.search(r"<thead>(.*?)</thead>", html[table:start], re.S)
    assert thead, "the matrix table must have a thead"
    last_row = re.findall(r"<tr[^>]*>(.*?)</tr>", thead.group(1), re.S)[-1]
    cells = []
    for th in re.findall(r"<th([^>]*)>", last_row):
        span = re.search(r'colspan="(\d+)"', th)
        hidden = "display:none" in th.replace(" ", "")
        cells.append(
            _HeaderCell(
                colspan=span.group(1) if span else None,
                display="none" if hidden else "",
            )
        )
    # Not an empty list. The `<thead>` and `<tr>` steps above raise when they
    # miss, but this one returns `[]` -- and `[]` is not a loud failure here,
    # it is a silent pass: `_expected_visible_columns()` sums to 0, the stub is
    # fed no header cells, and `matrixCols`'s own `reduce(..., 0)` also returns
    # 0, so the assertion downstream compares 0 to 0 and measures nothing. A
    # header row reformatted to `<TH ...>` would do it, and a browser would not
    # care.
    assert cells, (
        "no <th> cells parsed out of the matrix header row; the width check "
        "downstream would compare 0 against 0 and pass without measuring"
    )
    return cells


def _expected_visible_columns() -> int:
    """What a placeholder must span with no as-of in force."""
    return sum(int(cell.colspan or 1) for cell in _matrix_header_cells() if cell.display != "none")


class _ScriptBlocks(HTMLParser):
    """Collect the text of every ``<script>`` element.

    A parser rather than a pattern, because matching tags with a regular
    expression is a game you lose one case at a time. The version before this
    was `<script[^>]*>(.*?)</script>`, and it missed `<SCRIPT>`; adding
    ``re.I`` fixed that and the analyser immediately pointed at the next gap,
    `</script >` with a space. Casing, attributes and stray whitespace are all
    things `HTMLParser` already handles -- it lowercases tag names and switches
    to CDATA mode inside a script -- so this stops moving the defect around.
    """

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self._inside = False

    def handle_starttag(self, tag: str, attrs: object) -> None:  # noqa: ARG002
        if tag == "script":
            self._inside = True
            self.blocks.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._inside = False

    def handle_data(self, data: str) -> None:
        if self._inside:
            self.blocks[-1] += data


def _script() -> str:
    parser = _ScriptBlocks()
    parser.feed(WEBUI.read_text())
    parser.close()
    assert parser.blocks, "the page must carry a script block"
    return max(parser.blocks, key=len)


def _run_page() -> dict[str, object]:
    """Evaluate stub + page + probe under node and return what the probe saw."""
    node = shutil.which("node")
    assert node is not None
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as handle:
        cells = [cell._asdict() for cell in _matrix_header_cells()]
        stub = STUB.replace("__HEADER_CELLS__", json.dumps(cells))
        handle.write(stub + "\n" + _script() + "\n" + PROBE)
        path = handle.name
    result = subprocess.run(  # noqa: S603
        [node, path], capture_output=True, text=True, timeout=60, check=False
    )
    Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, (
        "the page's script threw while evaluating, which means the shipped UI "
        f"would be dead on arrival:\n{result.stderr[:3000]}"
    )
    report: dict[str, object] = json.loads(result.stdout.strip().splitlines()[-1])
    return report


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_script_evaluates_without_throwing() -> None:
    """Top-level evaluation must complete, and the whole file must be reachable."""
    report = _run_page()
    assert not report["missing"], (
        "these top-level names are undefined after the script ran, so evaluation "
        f"stopped before reaching them: {report['missing']}"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_matrix_panel_width_resolves_to_the_reader() -> None:
    """``SUITE_PANELS`` must hold the function, not a name that looks like one.

    This is the assertion the text-reading contract test cannot make. It sees
    the token ``matrixCols`` in the source and is satisfied; it cannot tell
    whether that token resolves at runtime, which is precisely what the
    temporal dead zone broke.
    """
    report = _run_page()
    assert report["matrix_panel_is_reader"], (
        "SUITE_PANELS declares a non-function width for matrix-rows; the matrix "
        "is the one table here whose width changes at runtime"
    )
    # And it must agree with the TABLE. This is the comparison the contract
    # test gave up when the literal became a call: asserting the string
    # `colspan="${matrixCols()}"` is present says nothing about what the
    # function returns. The stub is fed the real header cells, so a reader that
    # counted elements instead of summing colspans -- or that stopped excluding
    # the hidden column -- disagrees with the markup here.
    expected = _expected_visible_columns()
    assert report["matrix_panel_resolves"] == expected, (
        f"matrixCols() resolves to {report['matrix_panel_resolves']} against "
        f"{expected} visible columns in the matrix header"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_delta_cell_says_whose_change_it_is() -> None:
    """A later regrade's delta is shown, and is not claimed by the selected event.

    The arithmetic was fixed in the route; this is the half the reader sees.
    Three distinct states, and the middle one is the one that used to be
    indistinguishable from the first:

    * the selected event moved this row
    * something moved it, but not the selected event
    * nothing has moved it since the selected point

    Executed rather than pattern-matched, because the defect was a claim about
    WHICH event, and a test that greps for the word "affected" cannot tell the
    three apart.
    """
    report = _run_page()

    # The report is `dict[str, object]` because it carries several shapes; each
    # delta entry is `{text, cls, title}`.
    def _cell(key: str) -> dict[str, str]:
        return cast("dict[str, str]", report[key])

    mine = _cell("delta_this_event")
    later = _cell("delta_later_only")
    untouched = _cell("delta_untouched")

    # A real delta in both moved cases -- same number, since the rewound and
    # current rates fed in are the same.
    assert "-25.0" in mine["text"], mine
    assert "-25.0" in later["text"], later
    assert untouched["text"] == "not affected", untouched

    # But only the later-only case carries the marker, and the marker must NOT
    # carry a tooltip of its own -- a nested `title` shadows the cell's on
    # hover, so the reader who hovers the glyph that prompted the question
    # would get the marker's text instead of the sentence explaining it.
    assert '<span class="mv">*</span>' in later["text"], (
        "a delta this event did not cause must be marked, or the reader attributes "
        f"it to the event they picked: {later}"
    )
    assert "<span" not in mine["text"], mine
    assert "title=" not in later["text"], (
        f"the marker carries its own tooltip, which shadows the cell's: {later}"
    )

    # And the tooltips must make three different claims. The old one asserted
    # "the selected event did not touch any result in this configuration" for
    # every unaffected row, including rows a later regrade had moved.
    assert len({mine["title"], later["title"], untouched["title"]}) == 3
    assert "did not touch" in later["title"] and "LATER" in later["title"], later
    assert "no recorded regrade" in untouched["title"], untouched

    # The untouched row is dimmed; the moved ones carry a direction.
    assert untouched["cls"] == "dim", untouched
    assert mine["cls"] == "wr" and later["cls"] == "wr", (mine, later)
