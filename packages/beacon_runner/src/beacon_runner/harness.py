"""HarnessRunner EVAL mode entry point."""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from types import ModuleType
from typing import TYPE_CHECKING, cast

from beacon_graders.types import VerdictOutcome as GraderOutcome
from beacon_storage.config_identity import config_digest, config_label_of, model_id_of
from beacon_storage.models.runs import HarnessMode
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo

from beacon_runner.errors import (
    HarnessModeNotSupportedError,
    SutInvocationError,
    SutValidationError,
)
from beacon_runner.persistence import persist_result
from beacon_runner.types import ExecutionResult, ExecutionStep

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from beacon_ablation.engine import _ConfigLike, _HarnessRunnerLike, _SutLike
    from beacon_graders.composer import VerdictComposer
    from beacon_storage.models.attribution import Attribution
    from sqlalchemy.orm import Session, sessionmaker

    from beacon_runner.registry import SutRegistry
    from beacon_runner.sut import SolutionUnderTest
    from beacon_runner.types import EvalItem, SolutionConfig


_SUPPORTED_MODES = frozenset({HarnessMode.EVAL, HarnessMode.NIGHTLY_LOO})


@dataclass(frozen=True)
class _ItemTask:
    item: EvalItem
    attempt_idx: int


class HarnessRunner:
    """Top-level driver for a single EVAL run."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        registry: SutRegistry,
        composer: VerdictComposer,
        max_workers: int = 8,
        per_item_timeout_seconds: float = 180.0,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self.composer = composer
        self.max_workers = max_workers
        self.per_item_timeout_seconds = per_item_timeout_seconds

    def run_single(
        self,
        *,
        team_id: UUID,
        project_id: UUID,
        user_id: UUID,
        solution_record_id: UUID,
        items: Iterable[EvalItem],
        config: SolutionConfig,
        suite: str,
        dataset_version: str,
        pass_idx: int = 0,
        mode: HarnessMode = HarnessMode.EVAL,
        parent_sweep_id: UUID | None = None,
        sweep_arm: str | None = None,
    ) -> UUID:
        """Execute one EVAL or NIGHTLY_LOO pass over ``items`` and persist results.

        ``sweep_arm`` labels which arm of an ablation sweep this pass belongs to
        (``baseline``, ``no_<layer>``); it is part of the run's identity, so the
        arms of one sweep no longer collide at the same ``pass_idx``.
        """
        if mode not in _SUPPORTED_MODES:
            raise HarnessModeNotSupportedError(
                f"mode {mode.value} is not supported by HarnessRunner"
            )

        tasks = [_ItemTask(item=item, attempt_idx=pass_idx) for item in items]
        sut = self._resolve_and_validate_sut(solution_record_id, config, mode)
        run_id = self._create_run(
            team_id=team_id,
            project_id=project_id,
            user_id=user_id,
            solution_record_id=solution_record_id,
            suite=suite,
            dataset_version=dataset_version,
            pass_idx=pass_idx,
            mode=mode,
            parent_sweep_id=parent_sweep_id,
            sweep_arm=sweep_arm,
            config=config,
        )

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(
                        self._run_one,
                        sut,
                        task,
                        config,
                        team_id,
                        project_id,
                        run_id,
                    ): task
                    for task in tasks
                }
                for future in as_completed(futures):
                    future.result()
        except Exception as exc:
            with self.session_factory() as session:
                RunRepo(session).mark_failed(run_id, f"harness error: {exc!r}")
                session.commit()
            raise

        with self.session_factory() as session:
            RunRepo(session).mark_completed(run_id)
            session.commit()
        return run_id

    def _resolve_and_validate_sut(
        self,
        solution_record_id: UUID,
        config: SolutionConfig,
        mode: HarnessMode,
    ) -> SolutionUnderTest:
        with self.session_factory() as session:
            solution = SolutionRepo(session).get(solution_record_id)
            if solution is None:
                raise SutInvocationError(f"solution {solution_record_id} not found")
            sut = self.registry.get(solution.solution_id, solution.version)
            errors = sut.validate_config(config)
            if errors:
                raise SutValidationError("; ".join(errors))
            if mode.value not in solution.supported_modes:
                raise HarnessModeNotSupportedError(
                    f"solution {solution.solution_id}@{solution.version} "
                    f"does not declare support for {mode.value}"
                )
            return sut

    def _create_run(
        self,
        *,
        team_id: UUID,
        project_id: UUID,
        user_id: UUID,
        solution_record_id: UUID,
        suite: str,
        dataset_version: str,
        pass_idx: int,
        mode: HarnessMode,
        parent_sweep_id: UUID | None,
        sweep_arm: str | None,
        config: SolutionConfig,
    ) -> UUID:
        with self.session_factory() as session:
            payload = config.model_dump()
            run = RunRepo(session).create(
                team_id=team_id,
                project_id=project_id,
                solution_id=solution_record_id,
                suite=suite,
                dataset_version=dataset_version,
                mode=mode,
                pass_idx=pass_idx,
                parent_sweep_id=parent_sweep_id,
                sweep_arm=sweep_arm,
                config=payload,
                # The same identity API-registered runs get. Without it, every
                # in-process sweep run had NULL for all three and could not be
                # grouped into the matrix its own attribution rows feed.
                model_id=model_id_of(payload),
                config_label=config_label_of(payload),
                config_digest=config_digest(payload),
                created_by=user_id,
            )
            run_id = run.id
            RunRepo(session).mark_running(run_id)
            session.commit()
            return run_id

    def _run_one(
        self,
        sut: SolutionUnderTest,
        task: _ItemTask,
        config: SolutionConfig,
        team_id: UUID,
        project_id: UUID,
        run_id: UUID,
    ) -> None:
        item = task.item
        started = time.monotonic()
        try:
            exec_result = sut.invoke(item, config)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            exec_result = ExecutionResult(
                output={},
                output_kind="json",
                trace=ExecutionStep(
                    uuid=f"root-{item.item_id}",
                    name="harness_error",
                    level="workflow",
                    status="FAILED",
                    error=str(exc)[:1000],
                ),
                tokens_input=0,
                tokens_output=0,
                runtime_ms=elapsed_ms,
                error=f"sut_invoke_raised: {type(exc).__name__}: {exc!s}"[:1000],
            )

        try:
            verdicts, outcome = self.composer.compose(item, exec_result)
        except Exception as exc:
            verdicts = []
            outcome = GraderOutcome.ERROR
            exec_result = exec_result.model_copy(
                update={"error": f"composer_raised: {type(exc).__name__}: {exc!s}"[:1000]}
            )

        with self.session_factory() as session:
            persist_result(
                session,
                team_id=team_id,
                project_id=project_id,
                run_id=run_id,
                item=item,
                attempt_idx=task.attempt_idx,
                exec_result=exec_result,
                verdicts=verdicts,
                outcome=outcome,
            )
            session.commit()


def run_nightly_loo(
    *,
    runner: object,
    session: Session,
    sut: object,
    base_config: object,
    items: Sequence[object],
    suite: str,
    dataset_version: str,
    K: int,
    project_id: UUID,
    team_id: UUID,
    solution_id: UUID,
) -> list[Attribution]:
    """Run the NIGHTLY_LOO dispatcher via AttributionEngine.sweep."""
    from beacon_ablation.engine import AttributionEngine

    engine = AttributionEngine(session)
    return engine.sweep(
        sut=cast("_SutLike", sut),
        base_config=cast("_ConfigLike", base_config),
        items=items,
        suite=suite,
        dataset_version=dataset_version,
        K=K,
        project_id=project_id,
        team_id=team_id,
        solution_id=solution_id,
        harness_runner=cast("_HarnessRunnerLike", runner),
    )


def _register_modes_submodule() -> None:
    modes = ModuleType(f"{__name__}.modes")
    vars(modes)["run_nightly_loo"] = run_nightly_loo
    sys.modules[modes.__name__] = modes


_register_modes_submodule()
