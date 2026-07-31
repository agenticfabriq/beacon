import json

from beacon_ui.cli.formatters import format_output


def test_json_format_returns_valid_json() -> None:
    out = format_output([{"a": 1}], format="json")
    assert json.loads(out) == [{"a": 1}]


def test_table_format_returns_lines_for_rows() -> None:
    out = format_output([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], format="table")
    assert "a" in out and "b" in out
    assert "1" in out and "x" in out


def test_table_format_handles_empty_input() -> None:
    out = format_output([], format="table")
    assert "(no rows)" in out.lower() or out.strip() == ""
