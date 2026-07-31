"""CLI context file and environment-variable resolution."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_API_BASE = "http://localhost:8000"


@dataclass
class Context:
    team_id: str | None = None
    project_id: str | None = None
    api_base: str = DEFAULT_API_BASE
    api_key: str | None = None


def default_ctx_path() -> Path:
    """Return the path to the local CLI context file."""
    return Path(os.environ.get("BEACON_CTX_FILE", str(Path.home() / ".beacon" / "ctx.json")))


def _read_data(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_context(*, path: Path | None = None) -> Context:
    """Load the CLI context from disk, overlaying BEACON_* env vars."""
    data = _read_data(path or default_ctx_path())
    api_base = data.get("api_base")
    ctx = Context(
        team_id=data.get("team_id") if isinstance(data.get("team_id"), str) else None,
        project_id=data.get("project_id") if isinstance(data.get("project_id"), str) else None,
        api_base=api_base if isinstance(api_base, str) else DEFAULT_API_BASE,
        api_key=data.get("api_key") if isinstance(data.get("api_key"), str) else None,
    )

    if team_id := os.environ.get("BEACON_TEAM"):
        ctx.team_id = team_id
    if project_id := os.environ.get("BEACON_PROJECT"):
        ctx.project_id = project_id
    if api_base := os.environ.get("BEACON_API_BASE"):
        ctx.api_base = api_base
    if api_key := os.environ.get("BEACON_API_KEY"):
        ctx.api_key = api_key
    return ctx


def save_context(ctx: Context, *, path: Path | None = None) -> None:
    """Write the CLI context to disk with restricted permissions."""
    target = path or default_ctx_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(ctx), indent=2), encoding="utf-8")
    with suppress(OSError):
        target.chmod(0o600)
