from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

import pytest
from beacon_storage.models.production_traces import ProductionTrace
from beacon_ui.cli.main import app as cli_app
from click.testing import CliRunner
from sqlalchemy import select
from test_cli_teams import configure_cli_env  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    alice_key: str
    chat_to_data_id: UUID


def test_traces_import_uploads_via_rest(
    api_client: TestClient,
    world: _World,
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jsonl = tmp_path / "in.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "item_input": {"question": "Q1"},
                "item_output": {"sql": "SELECT 1"},
                "trace": {"name": "root", "children": []},
                "metadata": {"latency_ms": 100, "cost_usd": 0.001},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)

    result = CliRunner().invoke(
        cli_app,
        [
            "traces",
            "import",
            "--solution-id",
            "trace-sut",
            "--project",
            str(world.chat_to_data_id),
            "--format",
            "jsonl",
            "--input",
            str(jsonl),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Imported 1 trace" in result.stdout

    rows = list(
        session.scalars(
            select(ProductionTrace).where(
                ProductionTrace.project_id == world.chat_to_data_id,
                ProductionTrace.solution_id == "trace-sut",
            )
        )
    )
    assert len(rows) == 1
