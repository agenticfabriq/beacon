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

    Matched as a pattern rather than one literal: the previous version of this
    test pinned the exact string `"tokens_input": 0`, which a re-added output
    half or a different spacing would have walked straight past.
    """
    recipe = _runner_recipe(_page())

    assert not re.search(rf'"{field}"\s*:\s*\d', recipe), (
        f"the runner recipe must omit {field} rather than send a number"
    )


def test_the_recipe_says_omitting_cost_is_how_you_report_not_measuring_it() -> None:
    """Silence in a sample reads as an oversight unless the sample says why."""
    recipe = _runner_recipe(_page())

    assert "only if you" in recipe and "unrecorded" in recipe


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
