from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_ui.api.routes import api_keys as api_keys_routes
from beacon_ui.api.routes import attribution as attribution_routes
from beacon_ui.api.routes import auth as auth_routes
from beacon_ui.api.routes import ingest as ingest_routes
from beacon_ui.api.routes import leaderboards as leaderboards_routes
from beacon_ui.api.routes import matrix as matrix_routes
from beacon_ui.api.routes import me as me_routes
from beacon_ui.api.routes import regrade_history as regrade_history_routes
from beacon_ui.api.routes import results as results_routes
from beacon_ui.api.routes import runs as runs_routes
from beacon_ui.api.routes import suites as suites_routes
from beacon_ui.api.routes import team_members as team_members_routes
from beacon_ui.api.routes import team_solutions as team_solutions_routes
from beacon_ui.api.routes import teams as teams_routes
from beacon_ui.api.routes import webui as webui_routes

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    """Mount every Beacon API router on the given FastAPI application."""
    app.include_router(auth_routes.router)
    app.include_router(leaderboards_routes.router)
    app.include_router(attribution_routes.router)
    app.include_router(me_routes.router)
    app.include_router(api_keys_routes.router)
    app.include_router(teams_routes.router)
    app.include_router(team_members_routes.router)
    app.include_router(team_solutions_routes.router)
    app.include_router(suites_routes.router)
    app.include_router(runs_routes.router)
    app.include_router(results_routes.router)
    app.include_router(matrix_routes.router)
    app.include_router(regrade_history_routes.router)
    app.include_router(webui_routes.router)
    app.include_router(ingest_routes.router)
