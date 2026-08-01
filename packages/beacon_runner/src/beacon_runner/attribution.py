"""Adapter joining the ablation sweep port to the real ``HarnessRunner``.

``AttributionEngine`` drives sweeps through a narrow port: run one config over
some items and hand back the per-item result rows. That port deliberately knows
nothing about how beacon persists runs -- no users, no solution records, no
``HarnessMode``. ``HarnessRunner`` speaks exactly those terms. This module is
the one place the two vocabularies are translated.

The signature below is written against the *port's* types rather than beacon's
concrete ones so that mypy checks the conformance structurally at every call
site that passes this class to ``AttributionEngine.sweep``. That check is the
regression guard: the two halves previously drifted apart precisely because
nothing ever verified an implementation against the protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from beacon_storage.models.runs import HarnessMode
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.solutions import SolutionRepo

from beacon_runner.errors import SutIdentityMismatchError, SutInvocationError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from beacon_storage.models.runs import Result
    from sqlalchemy.orm import Session, sessionmaker

    from beacon_runner.harness import HarnessRunner
    from beacon_runner.types import EvalItem, SolutionConfig

__all__ = ["AttributionHarnessRunner"]


class _IdentityLike(Protocol):
    solution_id: str
    version: str


class _SutLike(Protocol):
    def identity(self) -> _IdentityLike:
        """Return the SUT identity the sweep is attributing results to."""
        ...


class AttributionHarnessRunner:
    """Runs ``AttributionEngine`` sweep arms through a real ``HarnessRunner``.

    The engine identifies a solution by the id it persists attributions against
    (the ``solutions`` row id); the harness calls the same value
    ``solution_record_id`` and resolves the SUT from it via the registry. The
    engine has no notion of an acting user, so one is supplied here.
    """

    def __init__(
        self,
        *,
        runner: HarnessRunner,
        session_factory: sessionmaker[Session],
        user_id: UUID,
    ) -> None:
        self.runner = runner
        self.session_factory = session_factory
        self.user_id = user_id

    def run_single(
        self,
        *,
        sut: object,
        config: object,
        items: Sequence[object],
        suite: str,
        dataset_version: str,
        mode: str,
        pass_idx: int,
        parent_sweep_id: UUID,
        project_id: UUID,
        team_id: UUID,
        solution_id: UUID,
        sweep_arm: str,
    ) -> UUID:
        """Execute one sweep arm pass and return the persisted run id."""
        self._assert_sut_matches_solution_record(cast("_SutLike", sut), solution_id)
        return self.runner.run_single(
            team_id=team_id,
            project_id=project_id,
            user_id=self.user_id,
            solution_record_id=solution_id,
            items=cast("Sequence[EvalItem]", items),
            config=cast("SolutionConfig", config),
            suite=suite,
            dataset_version=dataset_version,
            pass_idx=pass_idx,
            mode=HarnessMode(mode),
            parent_sweep_id=parent_sweep_id,
            sweep_arm=sweep_arm,
        )

    def list_results_for_run(self, run_id: UUID) -> Sequence[Result]:
        """Return the persisted per-item rows the metrics helpers aggregate."""
        with self.session_factory() as session:
            return ResultRepo(session).list_for_run(run_id)

    def _assert_sut_matches_solution_record(
        self,
        sut: _SutLike,
        solution_record_id: UUID,
    ) -> None:
        """Fail loudly when the swept SUT is not the one the harness will run.

        The harness resolves the SUT from the solution record, so a mismatch
        would silently attribute one SUT's layers to another's results.
        """
        identity = sut.identity()
        with self.session_factory() as session:
            solution = SolutionRepo(session).get(solution_record_id)
            if solution is None:
                raise SutInvocationError(f"solution {solution_record_id} not found")
            if (solution.solution_id, solution.version) != (identity.solution_id, identity.version):
                raise SutIdentityMismatchError(
                    f"sweep SUT is {identity.solution_id}@{identity.version} but solution "
                    f"record {solution_record_id} is "
                    f"{solution.solution_id}@{solution.version}"
                )
