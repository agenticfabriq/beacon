"""Minimal CLI for P2: `beacon suts register` and `beacon eval run`."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click
from beacon_graders.graders.dabstep_answer_matcher import DabstepAnswerMatcher
from beacon_graders.graders.hierarchical_rubric import HierarchicalRubricGrader
from beacon_graders.llm.anthropic_provider import AnthropicLLMProvider
from beacon_graders.llm.provider import JudgeCache
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.models.runs import HarnessMode
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from beacon_ui.cli import benchmarks_group, suites_group

from beacon_runner.composer_factory import composer_for_suite
from beacon_runner.errors import BeaconRunnerError, SutNotFoundError
from beacon_runner.harness import HarnessRunner
from beacon_runner.registry import default_registry
from beacon_runner.sut.resolver import (
    list_available_suts,
    load_sut_config,
    resolve_sut_factory,
)
from beacon_runner.types import EvalItem, SolutionConfig

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_graders.grader import Grader
    from beacon_graders.llm.provider import LLMProvider
    from sqlalchemy.orm import Session, sessionmaker

    from beacon_runner.sut import SolutionUnderTest


def _session_factory() -> sessionmaker[Session]:
    return make_session_factory(make_engine(os.environ["DATABASE_URL"]))


@click.group()
def main() -> None:
    """Beacon evaluation framework CLI."""


main.add_command(suites_group)
main.add_command(benchmarks_group)


@main.group()
def suts() -> None:
    """Solution-under-test catalog operations."""


@suts.command("available")
def suts_available() -> None:
    """List installable SUT implementations from the `beacon.suts` entry points.

    Distinct from the API-backed listing of solutions already registered to a
    team: this is what could be registered, not what has been.
    """
    names = list_available_suts()
    if not names:
        click.echo("no SUTs registered under the 'beacon.suts' entry-point group")
        return
    for name in names:
        click.echo(name)


def _build_sut(sut_spec: str, sut_config: str | None, owner_team_id: UUID) -> object:
    """Resolve and instantiate a SUT from an entry-point name or an import path."""
    try:
        factory = resolve_sut_factory(sut_spec)
        kwargs = load_sut_config(Path(sut_config) if sut_config else None)
        return factory(owner_team_id=owner_team_id, **kwargs)
    except BeaconRunnerError as exc:
        raise click.ClickException(str(exc)) from exc
    except TypeError as exc:
        raise click.ClickException(
            f"could not construct SUT {sut_spec!r} with the given --sut-config: {exc}"
        ) from exc


@suts.command("register")
@click.option("--team", "team_name", required=True)
@click.option("--as", "actor_email", required=True)
@click.option("--solution-id", required=True)
@click.option("--version", required=True)
@click.option(
    "--sut",
    "sut_spec",
    default=None,
    help=(
        "Entry-point name from `beacon suts list`, or an import path "
        "'package.module:Attribute'. Defaults to --solution-id."
    ),
)
@click.option(
    "--sut-config",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON object of constructor keyword arguments for the SUT.",
)
def suts_register(
    team_name: str,
    actor_email: str,
    solution_id: str,
    version: str,
    sut_spec: str | None,
    sut_config: str | None,
) -> None:
    """Register a SUT under a team catalog from its declared identity."""
    factory = _session_factory()
    with factory() as session:
        actor = UserRepo(session).get_by_email(actor_email)
        if actor is None:
            raise click.ClickException(f"user {actor_email} not found")
        set_current_user(session, actor.id)
        team = TeamRepo(session).get_by_name(team_name)
        if team is None:
            raise click.ClickException(f"team {team_name} not found")

        sut = cast("SolutionUnderTest", _build_sut(sut_spec or solution_id, sut_config, team.id))
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
@click.option("--suite-id", "suite_ref", required=True, type=click.UUID)
@click.option("--as", "actor_email", required=True)
@click.option("--solution-id", required=True)
@click.option("--version", required=True)
@click.option("--suite", required=True)
@click.option("--items", "items_path", required=True, type=click.Path(exists=True))
@click.option("--dataset-version", default="v0")
@click.option("--pass-idx", default=0, type=int)
@click.option("--max-workers", default=4, type=int)
@click.option(
    "--sut",
    "sut_spec",
    default=None,
    help=(
        "Entry-point name from `beacon suts list`, or an import path "
        "'package.module:Attribute'. Defaults to --solution-id."
    ),
)
@click.option(
    "--sut-config",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON object of constructor keyword arguments for the SUT.",
)
@click.option(
    "--benchmark-db-url",
    default=None,
    help="Database the suite's execution graders run candidate and gold SQL against.",
)
def eval_run(
    suite_ref: UUID,
    actor_email: str,
    solution_id: str,
    version: str,
    suite: str,
    items_path: str,
    dataset_version: str,
    pass_idx: int,
    max_workers: int,
    sut_spec: str | None,
    sut_config: str | None,
    benchmark_db_url: str | None,
) -> None:
    """Run a SUT against an inline JSONL list of EvalItems in EVAL mode."""
    items = _load_items(Path(items_path))
    factory = _session_factory()

    with factory() as session:
        actor = UserRepo(session).get_by_email(actor_email)
        if actor is None:
            raise click.ClickException(f"user {actor_email} not found")
        set_current_user(session, actor.id)
        suite_record = SuiteRepo(session).get(suite_ref)
        if suite_record is None:
            raise click.ClickException(f"suite {suite_ref} not found")
        solution = SolutionRepo(session).get_by_team_and_solution(
            suite_record.team_id,
            solution_id,
            version,
        )
        if solution is None:
            raise click.ClickException(
                f"solution {solution_id}@{version} not registered for team {suite_record.team_id}"
            )
        ids = (suite_record.team_id, actor.id, suite_record.id, solution.id)
    team_id, user_id, suite_record_id, solution_record_id = ids

    registry = default_registry()
    try:
        registry.get(solution_id, version)
    except SutNotFoundError:
        # Not already registered in this process, so build it from the seam.
        registry.register(
            cast("SolutionUnderTest", _build_sut(sut_spec or solution_id, sut_config, team_id))
        )

    benchmark_engine = make_engine(benchmark_db_url) if benchmark_db_url else None
    try:
        composer = composer_for_suite(
            suite,
            engine=benchmark_engine,
            judge_cache=_judge_cache(),
            fallback=_default_graders(),
        )
        runner = HarnessRunner(
            session_factory=factory,
            registry=registry,
            composer=composer,
            max_workers=max_workers,
        )
        try:
            run_id = runner.run_single(
                team_id=team_id,
                suite_id=suite_record_id,
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
    finally:
        if benchmark_engine is not None:
            benchmark_engine.dispose()
    click.echo(f"run_id={run_id}")


def _load_items(path: Path) -> list[EvalItem]:
    items: list[EvalItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            items.append(EvalItem.model_validate_json(stripped))
    return items


def _judge_cache() -> JudgeCache | None:
    """Return an LLM judge cache when an API key is configured, else None."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return JudgeCache(provider=cast("LLMProvider", AnthropicLLMProvider()))


def _default_graders() -> list[Grader]:
    """Graders used when no benchmark adapter claims the suite.

    Answer-matching only: this cannot grade ``output_kind="sql"``, which is why
    it must not be the whole story for a real SQL suite -- see composer_for_suite.
    """
    graders: list[Grader] = [DabstepAnswerMatcher()]
    cache = _judge_cache()
    if cache is not None:
        graders.append(HierarchicalRubricGrader(judge_cache=cache))
    return graders


if __name__ == "__main__":
    sys.exit(main())
