"""CLI output formatters."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table


def format_output(rows: list[dict[str, Any]] | dict[str, Any], *, format: str = "table") -> str:
    """Render rows as a Rich table or pretty JSON string."""
    if format == "json":
        return json.dumps(rows, indent=2, default=str)
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return "(no rows)"

    columns = list(rows[0].keys())
    table = Table(*columns)
    for row in rows:
        table.add_row(*[str(row.get(column, "")) for column in columns])

    console = Console(record=True, force_terminal=False, width=120)
    console.print(table)
    return console.export_text()
