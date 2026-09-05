"""The web UI's endpoint manifest, held against the API it is served with.

The UI declares every path it touches in one ENDPOINTS block. These tests parse
that block out of the shipped HTML and check each entry against the live
OpenAPI schema — so a UI referencing an endpoint that does not exist, or an
endpoint renamed out from under the UI, fails in CI. This replaces the
Streamlit AppTest harness as the API-matches-UI audit.
"""

from __future__ import annotations

import re
from importlib.resources import files
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_ENTRY = re.compile(r'\["(GET|POST|PATCH|DELETE)",\s*"(/v1/[^"]+)"\]')


def _manifest() -> list[tuple[str, str]]:
    page = (files("beacon_ui.webui") / "index.html").read_text(encoding="utf-8")
    block = re.search(r"const ENDPOINTS = \{(.*?)\};", page, re.S)
    assert block, "the UI must declare its endpoints in one ENDPOINTS block"
    entries = _ENTRY.findall(block.group(1))
    assert entries, "the ENDPOINTS block parsed to nothing"
    return [(method, path) for method, path in entries]


def test_every_ui_endpoint_is_served(api_client: TestClient) -> None:
    schema = api_client.get("/openapi.json").json()
    served = {
        (method.upper(), path)
        for path, methods in schema.get("paths", {}).items()
        for method in methods
    }

    missing = [entry for entry in _manifest() if entry not in served]
    assert not missing, f"the UI references endpoints the API does not serve: {missing}"


def test_the_ui_is_served_from_the_api_origin(api_client: TestClient) -> None:
    """Same origin, no CORS: the page and its API come from one process."""
    response = api_client.get("/ui")

    assert response.status_code == 200
    assert "ENDPOINTS" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_the_ui_is_not_api_surface(api_client: TestClient) -> None:
    """/ui is the client, not the API; it must not appear in the schema."""
    schema = api_client.get("/openapi.json").json()

    assert "/ui" not in schema.get("paths", {})


def _page() -> str:
    return (files("beacon_ui.webui") / "index.html").read_text(encoding="utf-8")


def _runner_recipe(page: str) -> str:
    """The push half of the cheat sheet -- the curl an operator copies."""
    recipe = re.search(r"# push one result:(.*?)# close the run", page, re.S)
    assert recipe, "the cheat sheet must carry a push recipe"
    return recipe.group(1)


def _matrix_column_count(page: str) -> int:
    """Columns in the results-matrix header, counting colspans."""
    thead = re.search(r"<thead>(.*?)</thead>", page, re.S)
    assert thead, "the matrix table must have a thead"
    last_row = re.findall(r"<tr[^>]*>(.*?)</tr>", thead.group(1), re.S)[-1]
    columns = 0
    for th in re.findall(r"<th[^>]*>", last_row):
        span = re.search(r'colspan="(\d+)"', th)
        columns += int(span.group(1)) if span else 1
    return columns


# Every function that writes the matrix table's body. NO_BENCH is shared with
# the runs table, which is 11 columns wide and correctly says so, so the
# invariant has to be scoped to one table rather than to the text.
_MATRIX_WRITERS = (
    r"const loadMatrix = guard\(async \(\) => \{(.*?)\n\}\);",
    r"function renderMatrixRows\(\) \{(.*?)\n\}",
    r"function matrixUnknown\(message\) \{(.*?)\n\}",
)


@pytest.mark.parametrize("pattern", _MATRIX_WRITERS)
def test_every_matrix_placeholder_spans_the_whole_table(pattern: str) -> None:
    """A placeholder narrower than the table renders a half-width sliver.

    Caught in the wild once already: the no-benchmark cell spanned 15 of 16
    columns after a column was added, because the width lives in three
    functions and nothing held them together. This is the thing that holds
    them.
    """
    page = _page()
    expected = _matrix_column_count(page)
    body = re.search(pattern, page, re.S)
    assert body, f"no function matched {pattern!r}"
    spans = [int(n) for n in re.findall(r'colspan="(\d+)"', body.group(1))]
    assert spans, "this function is expected to render a full-width placeholder"

    assert all(span == expected for span in spans), (
        f"placeholder spans {spans} against {expected} matrix columns"
    )


def test_a_failed_matrix_load_says_the_numbers_are_unknown() -> None:
    """A dead API must not look like a benchmark with nothing in it.

    Without this the fetch throws, the error bar scrolls out of sight at the
    top of the page, and the table keeps its headers over silence -- which is
    exactly what a finished load of an empty suite looks like. Zero and
    unknown are different claims and the table has to make only the true one.
    """
    page = _page()

    assert "matrixUnknown" in page, "the matrix needs a failure state, not just an error bar"
    assert "unknown, not zero" in page, "the failure state must not read as a score of zero"
    # Wired into the fetch, not merely defined: a handler nothing calls is decoration.
    body = re.search(r"const loadMatrix = guard\(async \(\) => \{(.*?)\n\}\);", page, re.S)
    assert body, "loadMatrix must be one guarded block"
    assert "matrixUnknown" in body.group(1), "loadMatrix must call it when the fetch throws"


def test_the_drilldown_does_not_add_two_token_fields_that_may_be_null() -> None:
    """`null + null` is `0` in JavaScript, so the naive sum invents a zero.

    Unrecorded cost is null all the way from the importer to the wire, and the
    one place it can quietly become a number again is the drill-down's
    `tokens_input + tokens_output`: JS coerces both nulls to 0 and prints
    "0 tokens" for an attempt whose cost nobody measured. The sum has to go
    through a guard that keeps absent absent.
    """
    page = _page()

    assert "detail.tokens_input + detail.tokens_output" not in page, (
        "the drill-down must not sum the token fields without a null guard"
    )
    assert "const tokenTotal =" in page, "the guard is expected to be a named helper"


@pytest.mark.parametrize("field", ["tokens_input", "tokens_output"])
def test_the_runner_recipe_does_not_teach_a_runner_to_claim_a_cost(field: str) -> None:
    """The cheat sheet is the onboarding path an external operator pastes.

    While tokens defaulted to 0 the sample's `"tokens_input": 0` was a harmless
    echo of the default. Now that omitted means unmeasured, sending a number is
    a claim about cost the runner following the recipe did not measure -- and a
    zero lands in the tokens column as a configuration that is free.

    Matched as a pattern rather than one literal: an earlier version of this
    test pinned the exact string `"tokens_input": 0`, which a re-added output
    half or a different spacing would have walked straight past.

    Scanned over the whole page rather than the push recipe alone. Scoping it
    to the recipe traded one weakness for another -- a sample payload added to
    the CLI block, or to a second `<pre>`, would teach the same fabricated zero
    with this green. The page has no other JSON literal for these fields; the
    drill-down reads them as `detail.tokens_input`, which this does not match.

    It matches a quoted JSON key only, so an unquoted `tokens_input: 0` or a
    `--tokens-input 0` flag would still slip past. That is the shape the page
    actually uses today, and pinning it is worth more than a looser pattern
    that would fire on the drill-down's reads.
    """
    assert not re.search(rf'"{field}"\s*:\s*\d', _page()), (
        f"no sample on the page may send {field} as a number"
    )


def test_the_recipe_says_omitting_cost_is_how_you_report_not_measuring_it() -> None:
    """Silence in a sample reads as an oversight unless the sample says why.

    Pinned as the contrast the operator has to come away with -- omitted and 0
    are different claims -- rather than as fragments unrelated prose could
    satisfy while the explanation itself went missing.
    """
    recipe = _runner_recipe(_page())

    assert "Omitted reads as unrecorded" in recipe
    assert "0 reads as free" in recipe


@pytest.mark.parametrize(
    "branch",
    [
        'return output + " output tokens, prompt not recorded"',
        'return input + " prompt tokens, output not recorded"',
    ],
)
def test_half_a_measurement_is_not_reported_as_nothing_recorded(branch: str) -> None:
    """The in-process SUT records output tokens and never the prompt.

    Refusing to total half a measurement is deliberate, but telling the
    operator nothing was recorded contradicts the row they are looking at.

    Pinned as the return statement rather than the wording: scoping to the
    helper is not enough on its own, because deleting the branch and leaving
    its text in a comment inside the same helper still matched.
    """
    body = re.search(r"const tokenTotal = \(input, output\) => \{(.*?)\n\};", _page(), re.S)
    assert body, "tokenTotal must be one arrow block"
    # Comments stripped: matching the raw text passed when the branch was
    # deleted and its return statement left behind as a comment.
    code = re.sub(r"//[^\n]*", "", body.group(1))

    assert branch in code


def _table_column_count(page: str, tbody_id: str) -> int:
    """Columns in the header of the table owning ``tbody_id``, counting colspans.

    Generalizes ``_matrix_column_count`` to any of the suite-scoped tables. The
    table is located by walking back from its tbody to the enclosing <table>,
    because the markup has no per-table id.
    """
    end = page.index(f'id="{tbody_id}"')
    start = page.rindex("<table", 0, end)
    thead = re.search(r"<thead>(.*?)</thead>", page[start:end], re.S)
    assert thead, f"the table owning {tbody_id} must have a thead"
    last_row = re.findall(r"<tr[^>]*>(.*?)</tr>", thead.group(1), re.S)[-1]
    columns = 0
    for th in re.findall(r"<th[^>]*>", last_row):
        span = re.search(r'colspan="(\d+)"', th)
        columns += int(span.group(1)) if span else 1
    return columns


def _declared_panels(page: str, block_name: str) -> list[tuple[str, int]]:
    block = re.search(rf"const {block_name} = \[(.*?)\];", page, re.S)
    assert block, f"the UI must declare its panels in one {block_name} block"
    entries = re.findall(r'\["([a-z-]+)",\s*(\d+)\]', block.group(1))
    assert entries, f"{block_name} must list at least one panel"
    return [(name, int(width)) for name, width in entries]


def _suite_panels(page: str) -> list[tuple[str, int]]:
    return _declared_panels(page, "SUITE_PANELS")


@pytest.mark.parametrize("block_name", ["SUITE_PANELS", "TEAM_PANELS"])
def test_every_declared_panel_placeholder_spans_its_whole_table(block_name: str) -> None:
    """Both panel groups, because the drift does not care which list it is in.

    TEAM_PANELS was added for the benchmarks table after that view was
    reported slow with no loading state. It is a FIFTH function writing a
    table body at a hardcoded width, and the invariant has to reach it for the
    same reason it had to reach the fourth.
    """
    page = _page()

    for tbody_id, declared in _declared_panels(page, block_name):
        actual = _table_column_count(page, tbody_id)
        assert declared == actual, (
            f"{block_name} declares colspan {declared} for {tbody_id}, "
            f"whose table has {actual} columns"
        )


def test_every_suite_panel_placeholder_spans_its_whole_table() -> None:
    """The loading placeholder must not render a half-width sliver.

    `test_every_matrix_placeholder_spans_the_whole_table` guards three
    functions that write the matrix body; `clearSuiteScopedPanels` is a FOURTH,
    and it writes three different tables at three different widths. That test
    passes while this one is absent -- not because the widths agree, but
    because it does not look here, which is the same drift it was written
    about, one function over.
    """
    page = _page()

    for tbody_id, declared in _suite_panels(page):
        actual = _table_column_count(page, tbody_id)
        assert declared == actual, (
            f"SUITE_PANELS declares colspan {declared} for {tbody_id}, "
            f"whose table has {actual} columns"
        )


def test_every_suite_scoped_loader_drops_a_stale_response() -> None:
    """A response for a suite the user has left must not be rendered.

    Clearing the panels on switch fixes the stale-content bug but not the
    ordering one: switching A -> B -> A faster than the requests return lets
    B's response land last and paint B's rows under A's name, which reads as
    authoritative. Each suite-scoped loader therefore captures `S.suiteGen`
    before its await and returns if it has moved.

    Pinned per loader rather than by counting, so ADDING a suite-scoped loader
    without the guard fails here instead of passing quietly.
    """
    page = _page()

    for loader in ("loadMatrix", "loadRuns", "loadQuestions"):
        body = re.search(rf"const {loader} = guard\(async \(\) => \{{(.*?)\n\}}\);", page, re.S)
        assert body, f"no {loader} found"
        text = body.group(1)
        assert "const gen = S.suiteGen;" in text, f"{loader} does not capture the suite generation"
        assert "if (gen !== S.suiteGen) return;" in text, f"{loader} does not drop a stale response"
        assert text.index("const gen = S.suiteGen;") < text.index("await api("), (
            f"{loader} captures the generation after its await, which captures the NEW suite"
        )
        assert text.index("await api(") < text.index("if (gen !== S.suiteGen) return;"), (
            f"{loader} checks the generation before awaiting, which cannot detect a switch"
        )


def test_the_suite_switch_bumps_the_generation_and_clears_the_panels() -> None:
    """Both halves, at the one site that changes the suite from the rail."""
    page = _page()
    handler = re.search(r'const suiteRow = t\.closest\("\[data-suite\]"\);(.*?)\n  \}', page, re.S)
    assert handler, "the suite-row click handler must exist"

    assert "S.suiteGen += 1;" in handler.group(1)
    assert "clearSuiteScopedPanels();" in handler.group(1)
    assert handler.group(1).index("clearSuiteScopedPanels();") < handler.group(1).index(
        'go("matrix")'
    ), "the panels must be cleared before the view is shown, or the old rows paint first"


def test_the_benchmarks_loader_says_it_is_loading_and_drops_a_stale_response() -> None:
    """Reported from use: the benchmarks view sat blank-then-populated slowly.

    It is slow for a findable reason -- one request per suite fetching up to
    500 FULL run objects only to take `.length`, N+1 in the number of
    benchmarks -- but a loader that awaits before writing must say so
    regardless. And it is TEAM-scoped, so a team switch races it exactly as a
    suite switch raced the matrix: without a generation token the previous
    team's suites paint under the new team's name.
    """
    page = _page()
    body = re.search(r"const loadBenchmarks = guard\(async \(\) => \{(.*?)\n\}\);", page, re.S)
    assert body, "no loadBenchmarks found"
    text = body.group(1)

    assert "clearTeamScopedPanels();" in text
    assert "const gen = S.teamGen;" in text
    assert "if (gen !== S.teamGen) return;" in text
    assert text.index("clearTeamScopedPanels();") < text.index("await api("), (
        "the panel must be blanked BEFORE the await, or the stale table stays up"
    )
    assert text.index("await api(") < text.index("if (gen !== S.teamGen) return;"), (
        "checking the generation before awaiting cannot detect a switch"
    )


def test_every_team_switch_bumps_the_team_generation() -> None:
    """Pinned per site: adding a fourth switch without the bump fails here.

    The initial resolution and the route restore are deliberately excluded --
    nothing is on screen yet at those points, so there is no stale panel to
    drop a response against.
    """
    page = _page()
    switches = [
        line for line in page.splitlines() if "S.teamId = " in line and "S.suites = []" in line
    ]
    assert len(switches) == 3, f"expected 3 interactive team switches, found {len(switches)}"

    for line in switches:
        assert "S.teamGen += 1;" in line, (
            f"team switch does not bump the generation: {line.strip()}"
        )


def test_the_benchmarks_loader_makes_ONE_request() -> None:
    """The N+1 must not come back, and it is easy to reintroduce.

    Rendering a run count per benchmark once meant a second request per suite
    asking for up to 500 whole run objects to take `.length` -- N+1 in the
    number of benchmarks, and a 288-run payload on bird_minidev_v2 for one
    number. It was reported as slow from use. The count now arrives on the
    suites response, so this loader awaits exactly one API call.

    Pinned as a count of `await api(` rather than by naming the endpoint,
    because the shape to prevent is "a second round trip", whichever endpoint
    it goes to.
    """
    page = _page()
    body = re.search(r"const loadBenchmarks = guard\(async \(\) => \{(.*?)\n\}\);", page, re.S)
    assert body, "no loadBenchmarks found"
    text = body.group(1)

    assert text.count("await api(") == 1, (
        f"loadBenchmarks awaits {text.count('await api(')} API calls; the run count "
        "comes back on the suites response and needs no second round trip"
    )
    assert "Promise.all" not in text
    assert "run_count" in text


def _verdict_line(page: str) -> str:
    """The drill-down's per-verdict template, comments removed.

    Comments are stripped because this file has already been fooled once by a
    deleted line surviving as a comment, and the verdict block carries several.
    """
    start = page.index("verdicts.map((v) =>")
    end = page.index('}).join("");', start)
    body = page[start:end]
    return "\n".join(
        segment for segment in body.split("\n") if not segment.strip().startswith("//")
    )


def test_the_drilldown_attributes_each_reading_to_a_version_and_a_conclusion() -> None:
    """WHO graded, at WHAT version, and WHAT it concluded -- all three.

    Verdicts are append-only and versioned, unique per
    `(result, metric, grader, version)`, so one result can hold several
    readings of the same metric. The panel used to render grader, metric and
    justification only: `grader_version`, `passed` and `value` are all on
    `VerdictOut` and none reached the screen, so two readings that DISAGREE
    were distinguishable solely by whatever their justifications said.

    Scoped to the interpolated template rather than searched page-wide,
    because an unscoped substring passes on a mutation that keeps the ternary
    and interpolates something else inside it -- and comments are stripped
    first, since a deleted line left behind as a comment has passed a test in
    this file before.
    """
    line = _verdict_line(_page())
    template = line[line.index("return `") :]

    for field in ("esc(v.grader)", "esc(v.grader_version)"):
        assert field in template, f"the verdict line must render {field}"

    # `passed` and `value` reach the screen through `reading`, so assert the
    # INTERPOLATION, not the mention. Reading the consts alone passes on a
    # mutation that computes the conclusion and then drops it from the output.
    assert "v.passed" in line and "v.value" in line, "the conclusion must be derived"
    assert "${reading" in template, (
        "the derived conclusion must be interpolated into the rendered line"
    )

    # The MAPPING, not just the mention. Swapping the ternary arms renders
    # every conclusion backwards on the one surface that shows it, and there is
    # no JS-execution harness here to catch that behaviourally -- so the
    # literal is what gets pinned.
    assert 'v.passed === true ? "PASS"' in line, (
        "a passing verdict must render PASS, not the other arm"
    )
    assert 'v.passed === false ? "FAIL"' in line, (
        "a failing verdict must render FAIL, not the other arm"
    )
    # And that the mapping REACHES the output. The literals above live in a
    # const, so dropping `verdict` from the array feeding `reading` renders no
    # conclusion at all while every other assertion here stays green.
    assert "[verdict, value]" in line, (
        "the PASS/FAIL reading must feed the interpolated conclusion"
    )
