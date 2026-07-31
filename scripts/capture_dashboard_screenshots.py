"""Capture Streamlit dashboard screenshots for the Phase 8 validation pack.

Prerequisites:
- Postgres running and migrated.
- `beacon demo seed` already run (so ACME / Globex exist with API keys).
- API running on `--api-base` (default ``http://localhost:8000``).
- Streamlit dashboard running on `--dashboard-url`
  (default ``http://localhost:8501``).
- Playwright installed: ``uv pip install playwright && uv run playwright
  install chromium``.

Output: PNG files under ``--out`` (default ``validation-artifacts/screenshots/``):

- ``00_login.png``
- ``01_project_overview.png`` ... ``09_project_settings.png``
- ``10_global_leaderboard.png``

Usage::

    DATABASE_URL=... uv run python scripts/capture_dashboard_screenshots.py \\
        --api-key <alice-demo-key> \\
        --api-base http://localhost:8000 \\
        --dashboard-url http://localhost:8501
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

PROJECT_TABS = (
    "Overview",
    "Runs",
    "Solutions",
    "Suites",
    "Attribution",
    "Cost/Accuracy",
    "Review Queue",
    "Members",
    "Settings",
)


@dataclass(frozen=True)
class CaptureConfig:
    """Captured screenshot job configuration."""

    dashboard_url: str
    api_base: str
    api_key: str
    out_dir: Path
    timeout_ms: int


def _resolve_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dashboard-url",
        default="http://localhost:8501",
        help="Streamlit dashboard URL (default: %(default)s).",
    )
    parser.add_argument(
        "--api-base",
        default="http://localhost:8000",
        help="Beacon API base URL (default: %(default)s).",
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="Alice (team_admin) demo API key. Printed by `beacon demo seed`.",
    )
    parser.add_argument(
        "--out",
        default="validation-artifacts/screenshots",
        help="Output directory for PNG screenshots (default: %(default)s).",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=15000,
        help="Per-action timeout in milliseconds (default: %(default)s).",
    )
    return parser


def _sign_in(page: Page, config: CaptureConfig) -> None:
    """Drive the Streamlit login form using `config`'s API base + key."""
    page.goto(config.dashboard_url, wait_until="networkidle")
    page.get_by_label("API base URL").fill(config.api_base)
    page.get_by_label("API key").fill(config.api_key)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_load_state("networkidle")


def _select_first_project(page: Page) -> None:
    """Click the first ACME project entry to enter the project workspace."""
    page.wait_for_selector("text=Projects", timeout=15000)
    acme = page.get_by_text("acme", exact=False).first
    acme.click()
    page.wait_for_load_state("networkidle")


def _shoot_tab(page: Page, tab: str, out_path: Path) -> None:
    """Activate the project tab named `tab` and write a full-page screenshot."""
    page.get_by_role("tab", name=tab).click()
    page.wait_for_load_state("networkidle")
    page.screenshot(path=str(out_path), full_page=True)


def _shoot_global_leaderboard(page: Page, out_path: Path) -> None:
    """Navigate to the cross-team leaderboard and screenshot it."""
    page.get_by_role("link", name="Global leaderboard").click()
    page.wait_for_load_state("networkidle")
    page.screenshot(path=str(out_path), full_page=True)


def capture(config: CaptureConfig) -> list[Path]:
    """Run the full capture flow and return the list of screenshot paths."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "playwright is required. Install with: "
            "`uv pip install playwright && uv run playwright install chromium`"
        ) from exc

    config.out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.set_default_timeout(config.timeout_ms)

            login_path = config.out_dir / "00_login.png"
            page.goto(config.dashboard_url, wait_until="networkidle")
            page.screenshot(path=str(login_path), full_page=True)
            shots.append(login_path)

            _sign_in(page, config)
            _select_first_project(page)

            for idx, tab in enumerate(PROJECT_TABS, start=1):
                safe = tab.lower().replace("/", "_").replace(" ", "_")
                tab_path = config.out_dir / f"{idx:02d}_project_{safe}.png"
                _shoot_tab(page, tab, tab_path)
                shots.append(tab_path)

            global_path = config.out_dir / f"{len(PROJECT_TABS) + 1:02d}_global_leaderboard.png"
            _shoot_global_leaderboard(page, global_path)
            shots.append(global_path)
        finally:
            browser.close()
    return shots


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point."""
    args = _resolve_argparser().parse_args(argv)
    config = CaptureConfig(
        dashboard_url=args.dashboard_url,
        api_base=args.api_base,
        api_key=args.api_key,
        out_dir=Path(args.out),
        timeout_ms=args.timeout_ms,
    )
    shots = capture(config)
    for shot in shots:
        print(shot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
