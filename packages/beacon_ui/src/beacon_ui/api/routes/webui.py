"""Serve the web UI: one static file over the same origin as the API.

Same origin on purpose — no CORS surface, and the UI's fetches hit exactly the
paths its ENDPOINTS manifest declares, which a test holds against the OpenAPI
schema. Excluded from that schema: it is not API surface, it is the client.
"""

from __future__ import annotations

from importlib.resources import files

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)


@router.get("/ui")
def web_ui() -> HTMLResponse:
    """Return the single-page UI."""
    page = (files("beacon_ui.webui") / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})
