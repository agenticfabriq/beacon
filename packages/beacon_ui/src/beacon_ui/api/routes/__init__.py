from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_ui.api.routes import attribution as attribution_routes
from beacon_ui.api.routes import auth as auth_routes
from beacon_ui.api.routes import gate as gate_routes
from beacon_ui.api.routes import leaderboards as leaderboards_routes
from beacon_ui.api.routes import me as me_routes
from beacon_ui.api.routes import project_settings as project_settings_routes
from beacon_ui.api.routes import project_solutions as project_solutions_routes
from beacon_ui.api.routes import projects as projects_routes
from beacon_ui.api.routes import registry as registry_routes
from beacon_ui.api.routes import review_queue as review_queue_routes
from beacon_ui.api.routes import runs as runs_routes
from beacon_ui.api.routes import suites as suites_routes
from beacon_ui.api.routes import team_members as team_members_routes
from beacon_ui.api.routes import team_solutions as team_solutions_routes
from beacon_ui.api.routes import teams as teams_routes
from beacon_ui.api.routes import traces as traces_routes

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    """Mount every Beacon API router on the given FastAPI application."""
    app.include_router(auth_routes.router)
    app.include_router(gate_routes.router)
    app.include_router(leaderboards_routes.router)
    app.include_router(attribution_routes.router)
    app.include_router(me_routes.router)
    app.include_router(teams_routes.router)
    app.include_router(team_members_routes.router)
    app.include_router(team_solutions_routes.router)
    app.include_router(projects_routes.router)
    app.include_router(project_settings_routes.router)
    app.include_router(project_solutions_routes.router)
    app.include_router(traces_routes.router)
    app.include_router(registry_routes.router)
    app.include_router(review_queue_routes.router)
    app.include_router(suites_routes.router)
    app.include_router(runs_routes.router)
