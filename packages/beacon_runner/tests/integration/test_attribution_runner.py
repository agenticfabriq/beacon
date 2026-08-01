"""Integration tests driving AttributionEngine sweeps through the real HarnessRunner."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_ablation.engine import AttributionEngine
from beacon_graders.composer import VerdictComposer
from beacon_graders.graders.dabstep_answer_matcher import DabstepAnswerMatcher
from beacon_runner.attribution import AttributionHarnessRunner
from beacon_runner.dummy_sut import DummySUT
from beacon_runner.errors import SutIdentityMismatchError
from beacon_runner.harness import HarnessRunner
from beacon_runner.registry import SutRegistry
from beacon_runner.types import EvalItem, SolutionConfig
from beacon_storage.db import make_session_factory
from beacon_storage.models.runs import HarnessMode, Run, RunStatus
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from sqlalchemy import select

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

SUITE = "loo_smoke_v1"
DATASET_VERSION = "v0"


def _items(n: int) -> list[EvalItem]:
    return [
        EvalItem(
            item_id=f"loo-{idx}",
            suite=SUITE,
            query={"question": "what is yes?"},
            ground_truth={"answer": "yes"},
            metadata={},
        )
        for idx in range(n)
    ]


def _bootstrap(
    engine: Engine,
    *,
    slug: str,
    solution_id: str = "dummy",
    version: str = DummySUT.VERSION,
) -> tuple[UUID, UUID, UUID, UUID]:
    factory = make_session_factory(engine)
    with factory() as session:
        user = UserRepo(session).create(email=f"{slug}@example.com", name=slug)
        team = TeamRepo(session).create(name=f"{slug}-team")
        project = ProjectRepo(session).create(
            team_id=team.id, name=f"{slug}-proj", created_by=user.id
        )
        sut = DummySUT(owner_team_id=team.id)
        solution = SolutionRepo(session).create(
            team_id=team.id,
            solution_id=solution_id,
            version=version,
            owner_team=team.id,
            summary="LOO sweep fixture",
            supported_modes=["EVAL", "NIGHTLY_LOO"],
            layers=[layer.model_dump(mode="json") for layer in sut.layers()],
            created_by=user.id,
        )
        session.commit()
        return team.id, user.id, project.id, solution.id


def _make_adapter(
    engine: Engine,
    *,
    team_id: UUID,
    user_id: UUID,
) -> AttributionHarnessRunner:
    factory = make_session_factory(engine)
    registry = SutRegistry()
    registry.register(DummySUT(owner_team_id=team_id))
    runner = HarnessRunner(
        session_factory=factory,
        registry=registry,
        composer=VerdictComposer(graders=[DabstepAnswerMatcher()]),
        max_workers=4,
    )
    return AttributionHarnessRunner(
        runner=runner,
        session_factory=factory,
        user_id=user_id,
    )


def test_loo_sweep_through_real_harness_persists_attributions(engine: Engine) -> None:
    """The shipped attribution engine drives the shipped harness end to end (B2)."""
    factory = make_session_factory(engine)
    team_id, user_id, project_id, solution_record_id = _bootstrap(engine, slug="loo")
    adapter = _make_adapter(engine, team_id=team_id, user_id=user_id)
    sut = DummySUT(owner_team_id=team_id)
    k = 2

    with factory() as session:
        attributions = AttributionEngine(session).sweep(
            sut=sut,
            base_config=SolutionConfig(
                model_id="dummy-m",
                prompt_version="loo-v0",
                layers_enabled={"ontology": True, "retry_loop": True},
            ),
            items=_items(12),
            suite=SUITE,
            dataset_version=DATASET_VERSION,
            K=k,
            project_id=project_id,
            team_id=team_id,
            solution_id=solution_record_id,
            harness_runner=adapter,
        )
        session.commit()
        sweep_id = attributions[0].sweep_id
        layer_names = {row.layer_name for row in attributions}
        assert layer_names == {"ontology", "retry_loop"}
        for row in attributions:
            assert row.methodology == "LOO"
            assert row.bh_adjusted_p is not None
            assert set(row.pass_at_k_baseline) == {str(i) for i in range(1, k + 1)}

    with factory() as session:
        runs = list(session.scalars(select(Run).where(Run.parent_sweep_id == sweep_id)))
        # 3 arms (baseline + one per ablated layer) x K passes.
        assert len(runs) == 3 * k
        assert {run.sweep_arm for run in runs} == {"baseline", "no_ontology", "no_retry_loop"}
        assert all(run.mode == HarnessMode.NIGHTLY_LOO for run in runs)
        assert all(run.status == RunStatus.COMPLETED for run in runs)

        for run in runs:
            results = ResultRepo(session).list_for_run(run.id)
            assert len(results) == 12
            assert all(result.outcome is not None for result in results)


def test_adapter_lists_persisted_results_for_a_run(engine: Engine) -> None:
    """list_results_for_run returns the rows the metrics helpers duck-type."""
    team_id, user_id, project_id, solution_record_id = _bootstrap(engine, slug="listres")
    adapter = _make_adapter(engine, team_id=team_id, user_id=user_id)
    sut = DummySUT(owner_team_id=team_id)

    run_id = adapter.run_single(
        sut=sut,
        config=SolutionConfig(
            model_id="dummy-m",
            prompt_version="v0",
            layers_enabled={"ontology": True, "retry_loop": True},
        ),
        items=_items(4),
        suite=SUITE,
        dataset_version=DATASET_VERSION,
        mode="NIGHTLY_LOO",
        pass_idx=1,
        parent_sweep_id=solution_record_id,
        project_id=project_id,
        team_id=team_id,
        solution_id=solution_record_id,
        sweep_arm="baseline",
    )

    rows = adapter.list_results_for_run(run_id)
    assert len(rows) == 4
    for row in rows:
        assert row.attempt_idx == 1
        assert isinstance(row.tokens_input, int)
        assert isinstance(row.tokens_output, int)
        assert isinstance(row.runtime_ms, int)
        assert row.outcome is not None


def test_adapter_rejects_sut_that_does_not_match_the_solution_record(engine: Engine) -> None:
    """The engine's sut argument is cross-checked, not silently ignored."""
    team_id, user_id, project_id, solution_record_id = _bootstrap(
        engine,
        slug="mismatch",
        version="9.9.9",
    )
    adapter = _make_adapter(engine, team_id=team_id, user_id=user_id)

    with pytest.raises(SutIdentityMismatchError):
        adapter.run_single(
            sut=DummySUT(owner_team_id=team_id),
            config=SolutionConfig(model_id="dummy-m"),
            items=_items(1),
            suite=SUITE,
            dataset_version=DATASET_VERSION,
            mode="NIGHTLY_LOO",
            pass_idx=0,
            parent_sweep_id=solution_record_id,
            project_id=project_id,
            team_id=team_id,
            solution_id=solution_record_id,
            sweep_arm="baseline",
        )
