"""`beacon demo` fixture commands."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import click
from beacon_iam.auth.api_key import generate_api_key, hash_api_key
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_storage.db import make_engine, make_session_factory, session_scope
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.runs import HarnessMode, ResultStatus, Run, RunStatus, VerdictOutcome
from beacon_storage.models.tenancy import ApiKey, Role, ScopeKind
from beacon_storage.repository.api_keys import ApiKeyRepo
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from sqlalchemy import select

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_storage.models.eval_items import EvalItem
    from beacon_storage.models.solutions import Solution
    from beacon_storage.models.suites import Suite
    from beacon_storage.models.tenancy import Team, User
    from sqlalchemy.orm import Session

DEMO_SUITE = "bird_minidev_v2"
# A focused subset alongside the full demo suite. It used to be the PR gate's
# curated_50_* suite; the gate is gone and nothing reads the naming convention.
FOCUS_SUITE = "demo_focus_suite"
DATASET_VERSION = "demo-v1"


@dataclass(frozen=True)
class DemoSeedSummary:
    acme_team_id: UUID
    globex_team_id: UUID
    eval_item_count: int
    curated_item_count: int
    baseline_run_id: UUID
    api_keys: dict[str, str | None]


@click.group()
def cli() -> None:
    """Demo fixture commands."""


@cli.command("seed", help="Seed the ACME / Globex teams + benchmarks + baseline run.")
@click.option(
    "--api-base",
    default="http://localhost:8000",
    show_default=True,
    help="Beacon API base URL to display in the seed summary.",
)
@click.option(
    "--database-url",
    default=None,
    help="Database URL. Defaults to DATABASE_URL.",
)
def seed(api_base: str, database_url: str | None) -> None:
    """Seed ACME and Globex demo teams, benchmarks, and a baseline run."""
    resolved_url = database_url or os.environ.get("DATABASE_URL")
    if not resolved_url:
        raise click.ClickException("DATABASE_URL or --database-url is required")

    engine = make_engine(resolved_url)
    try:
        factory = make_session_factory(engine)
        with session_scope(factory) as session:
            summary = seed_demo_data(session)
    finally:
        engine.dispose()

    click.echo("Demo seed complete.")
    click.echo(f"api_base={api_base}")
    click.echo(f"acme_team_id={summary.acme_team_id}")
    click.echo(f"globex_team_id={summary.globex_team_id}")
    click.echo(f"eval_items={summary.eval_item_count}")
    click.echo(f"curated_items={summary.curated_item_count}")
    click.echo(f"baseline_run_id={summary.baseline_run_id}")
    click.echo("api_keys:")
    for email, key in summary.api_keys.items():
        if key is None:
            click.echo(f"  {email}: existing key not reprinted")
        else:
            click.echo(f"  {email}: {key}")


def seed_demo_data(session: Session) -> DemoSeedSummary:
    """Populate the database with demo teams, suites, items, and a baseline run."""
    alice = _ensure_user(session, subject="alice", email="alice@example.com", name="Alice")
    carol = _ensure_user(session, subject="carol", email="carol@example.com", name="Carol")
    acme = _ensure_team(session, name="acme", description="ACME demo team")
    globex = _ensure_team(session, name="globex", description="Globex demo team")
    memberships = MembershipRepo(session)
    _ensure_membership(
        memberships,
        user_id=alice.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=acme.id,
        role=Role.TEAM_ADMIN,
        granted_by=alice.id,
    )
    _ensure_membership(
        memberships,
        user_id=carol.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=globex.id,
        role=Role.TEAM_ADMIN,
        granted_by=alice.id,
    )
    acme_solution = _ensure_solution(
        session,
        team=acme,
        solution_id="chat-to-data-v3.2",
        version="0.1",
        summary="Demo Chat-to-Data SUT",
        created_by=alice.id,
    )
    _ensure_solution(
        session,
        team=globex,
        solution_id="globex-sql-v1.4",
        version="0.1",
        summary="Demo Globex SQL SUT",
        created_by=carol.id,
    )
    items = _ensure_bird_items(session, created_by=alice.id)
    _ensure_suite(
        session,
        team=acme,
        name=DEMO_SUITE,
        method="manual",
        metadata={"source": "demo-seed", "dataset_version": DATASET_VERSION},
        item_ids=[item.item_id for item in items],
        created_by=alice.id,
    )
    curated_suite = _ensure_suite(
        session,
        team=acme,
        name=FOCUS_SUITE,
        method="manual",
        metadata={"source": DEMO_SUITE, "dataset_version": DATASET_VERSION},
        item_ids=[item.item_id for item in items],
        created_by=alice.id,
    )
    _ensure_suite(
        session,
        team=globex,
        name=DEMO_SUITE,
        method="manual",
        metadata={"source": "demo-seed", "dataset_version": DATASET_VERSION},
        item_ids=[item.item_id for item in items],
        created_by=carol.id,
    )
    baseline = _ensure_baseline_runs(
        session,
        team=acme,
        solution=acme_solution,
        suite=curated_suite,
        items=items,
        created_by=alice.id,
    )
    if curated_suite.baseline_run_id is None:
        curated_suite.baseline_run_id = baseline.id

    return DemoSeedSummary(
        acme_team_id=acme.id,
        globex_team_id=globex.id,
        eval_item_count=len(items),
        curated_item_count=len(SuiteRepo(session).list_item_ids(curated_suite.id)),
        baseline_run_id=baseline.id,
        api_keys={
            "alice@example.com": _ensure_api_key(session, user=alice, label="demo-alice"),
            "carol@example.com": _ensure_api_key(session, user=carol, label="demo-carol"),
        },
    )


def _ensure_user(session: Session, *, subject: str, email: str, name: str) -> User:
    return UserService(session).upsert_from_oidc(
        OidcClaims(subject=subject, email=email, name=name)
    )


def _ensure_team(session: Session, *, name: str, description: str) -> Team:
    repo = TeamRepo(session)
    team = repo.get_by_name(name)
    if team is not None:
        return team
    return repo.create(name=name, description=description)


def _ensure_membership(
    repo: MembershipRepo,
    *,
    user_id: UUID,
    scope_kind: ScopeKind,
    scope_id: UUID,
    role: Role,
    granted_by: UUID,
) -> None:
    for membership in repo.list_for_user(user_id):
        if membership.scope_kind == scope_kind and membership.scope_id == scope_id:
            if membership.role != role:
                repo.grant(
                    user_id=user_id,
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                    role=role,
                    granted_by=granted_by,
                )
            return
    repo.grant(
        user_id=user_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
        role=role,
        granted_by=granted_by,
    )


def _ensure_solution(
    session: Session,
    *,
    team: Team,
    solution_id: str,
    version: str,
    summary: str,
    created_by: UUID,
) -> Solution:
    repo = SolutionRepo(session)
    solution = repo.get_by_team_and_solution(team.id, solution_id, version)
    if solution is not None:
        return solution
    return repo.create(
        team_id=team.id,
        solution_id=solution_id,
        version=version,
        owner_team=team.id,
        summary=summary,
        supported_modes=["EVAL", "NIGHTLY_LOO"],
        layers=[
            {
                "name": "ontology",
                "description": "business ontology grounding",
                "ablation_semantic": "disabled",
                "instrumentation": "native",
            },
            {
                "name": "retry_loop",
                "description": "self-correction retry loop",
                "ablation_semantic": "disabled",
                "instrumentation": "native",
            },
        ],
        created_by=created_by,
    )


def _ensure_bird_items(session: Session, *, created_by: UUID) -> list[EvalItem]:
    repo = EvalItemRepo(session)
    items: list[EvalItem] = []
    for idx in range(1, 51):
        item, _created = repo.upsert_by_question_hash(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite=DEMO_SUITE,
            team_id=None,
            dataset_version=DATASET_VERSION,
            question_hash=f"demo:{DEMO_SUITE}:{idx:03d}",
            item_input={
                "question": f"Demo BIRD Mini-Dev question {idx}",
                "db_id": "california_schools",
            },
            gold_answer={"sql": f"SELECT {idx} AS answer"},
            item_metadata={
                "source": "demo-seed",
                "difficulty": "easy" if idx <= 30 else "medium",
            },
            created_by=created_by,
        )
        items.append(item)
    return items


def _ensure_suite(
    session: Session,
    *,
    team: Team,
    name: str,
    method: str,
    metadata: dict[str, object],
    item_ids: list[UUID],
    created_by: UUID,
) -> Suite:
    repo = SuiteRepo(session)
    suite = repo.get_by_team_and_name(team.id, name)
    if suite is None:
        suite = repo.create(
            team_id=team.id,
            name=name,
            description=f"Demo suite {name}",
            method=method,
            suite_metadata=metadata,
            created_by=created_by,
        )
    repo.add_items(suite_id=suite.id, item_ids=item_ids)
    return suite


def _ensure_baseline_runs(
    session: Session,
    *,
    team: Team,
    solution: Solution,
    suite: Suite,
    items: list[EvalItem],
    created_by: UUID,
) -> Run:
    repo = RunRepo(session)
    baseline: Run | None = None
    for pass_idx in range(3):
        run = _get_baseline_run(
            session,
            suite_id=suite.id,
            solution_id=solution.id,
            pass_idx=pass_idx,
        )
        if run is None:
            run = repo.create(
                team_id=team.id,
                suite_id=suite.id,
                solution_id=solution.id,
                suite=suite.name,
                dataset_version=DATASET_VERSION,
                mode=HarnessMode.NIGHTLY_LOO,
                pass_idx=pass_idx,
                config={"source": "demo-seed"},
                created_by=created_by,
            )
        _ensure_baseline_results(session, run=run, items=items, pass_idx=pass_idx)
        if run.status != RunStatus.COMPLETED:
            repo.mark_completed(run.id)
        if pass_idx == 0:
            baseline = run
    assert baseline is not None
    return baseline


def _get_baseline_run(
    session: Session,
    *,
    suite_id: UUID,
    solution_id: UUID,
    pass_idx: int,
) -> Run | None:
    return session.scalar(
        select(Run).where(
            Run.suite_id == suite_id,
            Run.solution_id == solution_id,
            Run.dataset_version == DATASET_VERSION,
            Run.mode == HarnessMode.NIGHTLY_LOO,
            Run.pass_idx == pass_idx,
            Run.parent_sweep_id.is_(None),
        )
    )


def _ensure_baseline_results(
    session: Session,
    *,
    run: Run,
    items: list[EvalItem],
    pass_idx: int,
) -> None:
    repo = ResultRepo(session)
    existing = {result.item_id for result in repo.list_for_run(run.id)}
    for idx, item in enumerate(items, start=1):
        item_id = str(item.item_id)
        if item_id in existing:
            continue
        passed = (idx + pass_idx) % 10 != 0
        repo.create(
            team_id=run.team_id,
            run_id=run.id,
            item_id=item_id,
            attempt_idx=0,
            output={"sql": item.gold_answer["sql"] if passed and item.gold_answer else "SELECT 0"},
            output_kind="sql",
            tokens_input=250 + idx,
            tokens_output=50,
            runtime_ms=900 + idx,
            status=ResultStatus.COMPLETED,
            outcome=VerdictOutcome.PASS if passed else VerdictOutcome.FAIL,
            error=None,
        )


def _ensure_api_key(session: Session, *, user: User, label: str) -> str | None:
    existing = session.scalar(
        select(ApiKey).where(
            ApiKey.user_id == user.id,
            ApiKey.label == label,
            ApiKey.revoked_at.is_(None),
        )
    )
    if existing is not None:
        return None
    key = generate_api_key(prefix="bcn_demo")
    ApiKeyRepo(session).create(user_id=user.id, key_hash=hash_api_key(key), label=label)
    return key
