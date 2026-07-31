"""Minimal CLI for P2: `beacon suts register` and `beacon eval run`."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click
from beacon_graders.composer import VerdictComposer
from beacon_graders.graders.dabstep_answer_matcher import DabstepAnswerMatcher
from beacon_graders.graders.hierarchical_rubric import HierarchicalRubricGrader
from beacon_graders.llm.anthropic_provider import AnthropicLLMProvider
from beacon_graders.llm.provider import JudgeCache
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.models.runs import HarnessMode
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from beacon_ui.cli import benchmarks_group, registry_group, suites_group, traces_group

from beacon_runner.dummy_sut import DummySUT
from beacon_runner.errors import BeaconRunnerError
from beacon_runner.harness import HarnessRunner
from beacon_runner.registry import default_registry
from beacon_runner.types import EvalItem, SolutionConfig

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_graders.grader import Grader
    from beacon_graders.llm.provider import LLMProvider
    from sqlalchemy.orm import Session, sessionmaker


def _session_factory() -> sessionmaker[Session]:
    return make_session_factory(make_engine(os.environ["DATABASE_URL"]))


@click.group()
def main() -> None:
    """Beacon evaluation framework CLI."""


main.add_command(registry_group)
main.add_command(suites_group)
main.add_command(traces_group)
main.add_command(benchmarks_group)


@main.group()
def suts() -> None:
    """Solution-under-test catalog operations."""


@suts.command("register")
@click.option("--team", "team_name", required=True)
@click.option("--as", "actor_email", required=True)
@click.option("--solution-id", required=True)
@click.option("--version", required=True)
def suts_register(team_name: str, actor_email: str, solution_id: str, version: str) -> None:
    """Register the built-in DummySUT under a team catalog."""
    if solution_id != "dummy":
        raise click.ClickException(f"P2 only ships the `dummy` SUT; got {solution_id!r}")

    factory = _session_factory()
    with factory() as session:
        actor = UserRepo(session).get_by_email(actor_email)
        if actor is None:
            raise click.ClickException(f"user {actor_email} not found")
        set_current_user(session, actor.id)
        team = TeamRepo(session).get_by_name(team_name)
        if team is None:
            raise click.ClickException(f"team {team_name} not found")

        sut = DummySUT(owner_team_id=team.id)
        default_registry().register(sut)
        existing = SolutionRepo(session).get_by_team_and_solution(team.id, solution_id, version)
        if existing is not None:
            click.echo(f"already registered: {existing.id}")
            return

        record = SolutionRepo(session).create(
            team_id=team.id,
            solution_id=solution_id,
            version=version,
            owner_team=team.id,
            summary=sut.identity().summary,
            supported_modes=list(sut.identity().supported_modes),
            layers=[layer.model_dump(mode="json") for layer in sut.layers()],
            created_by=actor.id,
        )
        session.commit()
        click.echo(f"registered solution {solution_id}@{version} as {record.id}")


@main.group("eval")
def eval_group() -> None:
    """Eval execution."""


@eval_group.command("run")
@click.option("--project-id", required=True, type=click.UUID)
@click.option("--as", "actor_email", required=True)
@click.option("--solution-id", required=True)
@click.option("--version", required=True)
@click.option("--suite", required=True)
@click.option("--items", "items_path", required=True, type=click.Path(exists=True))
@click.option("--dataset-version", default="v0")
@click.option("--pass-idx", default=0, type=int)
@click.option("--max-workers", default=4, type=int)
def eval_run(
    project_id: UUID,
    actor_email: str,
    solution_id: str,
    version: str,
    suite: str,
    items_path: str,
    dataset_version: str,
    pass_idx: int,
    max_workers: int,
) -> None:
    """Run a SUT against an inline JSONL list of EvalItems in EVAL mode."""
    items = _load_items(Path(items_path))
    factory = _session_factory()

    with factory() as session:
        actor = UserRepo(session).get_by_email(actor_email)
        if actor is None:
            raise click.ClickException(f"user {actor_email} not found")
        set_current_user(session, actor.id)
        project = ProjectRepo(session).get(project_id)
        if project is None:
            raise click.ClickException(f"project {project_id} not found")
        solution = SolutionRepo(session).get_by_team_and_solution(
            project.team_id,
            solution_id,
            version,
        )
        if solution is None:
            raise click.ClickException(
                f"solution {solution_id}@{version} not registered for team {project.team_id}"
            )
        ids = (project.team_id, actor.id, project.id, solution.id)
    team_id, user_id, project_record_id, solution_record_id = ids

    registry = default_registry()
    try:
        registry.get(solution_id, version)
    except Exception:
        if solution_id != "dummy":
            raise click.ClickException(
                f"no in-process SUT registered for {solution_id}@{version}"
            ) from None
        registry.register(DummySUT(owner_team_id=team_id))

    runner = HarnessRunner(
        session_factory=factory,
        registry=registry,
        composer=_default_composer(),
        max_workers=max_workers,
    )
    try:
        run_id = runner.run_single(
            team_id=team_id,
            project_id=project_record_id,
            user_id=user_id,
            solution_record_id=solution_record_id,
            items=items,
            config=SolutionConfig(model_id=solution_id, prompt_version="v0", layers_enabled={}),
            suite=suite,
            dataset_version=dataset_version,
            pass_idx=pass_idx,
            mode=HarnessMode.EVAL,
        )
    except BeaconRunnerError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"run_id={run_id}")


def _load_items(path: Path) -> list[EvalItem]:
    items: list[EvalItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            items.append(EvalItem.model_validate_json(stripped))
    return items


def _default_composer() -> VerdictComposer:
    graders: list[Grader] = [DabstepAnswerMatcher()]
    if os.environ.get("ANTHROPIC_API_KEY"):
        provider = AnthropicLLMProvider()
        graders.append(
            HierarchicalRubricGrader(judge_cache=JudgeCache(provider=cast("LLMProvider", provider)))
        )
    return VerdictComposer(graders=graders)


if __name__ == "__main__":
    sys.exit(main())
