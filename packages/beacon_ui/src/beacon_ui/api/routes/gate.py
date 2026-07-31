"""PR_GATE webhook API route."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_runner.errors import BeaconRunnerError
from beacon_runner.service.gate import GateResult, GateService
from beacon_storage.models.project_solutions import ProjectSolution
from beacon_storage.models.suites import Suite  # noqa: TC002
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.gate import GateDecision, GateRequest

router = APIRouter(prefix="/v1/projects", tags=["gate"])


def _curated_suite_for_project(session: Session, project_id: UUID) -> Suite:
    for suite in SuiteRepo(session).list_for_project(project_id):
        subset_tag = suite.suite_metadata.get("subset_tag")
        if (
            suite.method == "separability_gain"
            and isinstance(subset_tag, str)
            and subset_tag.startswith("curated_50_")
        ):
            return suite
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "project has no curated_50_* suite for PR_GATE",
    )


def _assert_project_solution_linked(
    session: Session,
    *,
    project_id: UUID,
    solution_id: UUID,
) -> None:
    if session.get(ProjectSolution, (project_id, solution_id)) is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "solution is not attached to this project",
        )


def _decision(
    result: GateResult,
    gate_policy: dict[str, object],
) -> Literal["pass", "fail", "warn"]:
    if result.outcome == "PASS":
        return "pass"
    if gate_policy.get("mode") == "warn":
        return "warn"
    return "fail"


@router.post(
    "/{project_id}/gate",
    response_model=GateDecision,
    summary="PR_GATE webhook",
)
@requires(Permission.PROJECT_RUN_EVAL)
def gate(
    project_id: UUID,
    body: GateRequest,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.PROJECT_RUN_EVAL, scope_kind="project")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> GateDecision:
    """Run the PR_GATE for a project commit and return the pass/warn/fail decision."""
    project = ProjectRepo(session).get(project_id)
    assert project is not None
    if project.baseline_run_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "project has no pinned baseline; PATCH /settings to set one",
        )

    solution = SolutionRepo(session).get(body.solution_id)
    if solution is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"solution {body.solution_id} not found",
        )
    if solution.team_id != project.team_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "solution belongs to a different team than this project",
        )
    _assert_project_solution_linked(
        session,
        project_id=project_id,
        solution_id=body.solution_id,
    )
    suite = _curated_suite_for_project(session, project_id)

    try:
        result = GateService(session).run_gate(
            project_id=project_id,
            sut_id=body.solution_id,
            suite_id=suite.id,
            commit_sha=body.commit_sha,
            baseline_run_id=project.baseline_run_id,
        )
    except (BeaconRunnerError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return GateDecision(
        decision=_decision(result, project.gate_policy or {}),
        run_id=result.new_run_id,
        baseline_run_id=result.baseline_run_id,
        delta={
            "pass_at_3": result.delta_pass_at_3,
            "pass_hat_3": result.delta_pass_hat_3,
            "mcnemar_p": result.mcnemar_p,
            "bh_adjusted_p": result.bh_adjusted_p,
        },
        reason=result.reason,
    )
