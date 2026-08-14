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
