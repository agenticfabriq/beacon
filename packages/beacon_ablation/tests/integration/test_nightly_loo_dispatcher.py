"""Integration test for the NIGHTLY_LOO dispatcher."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from beacon_storage.models.attribution import Attribution
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _SweepFixtures(Protocol):
    sut: object
    base_config: object
    items: list[object]
    runner: object
    suite_id: UUID
    team_id: UUID
    solution_id: UUID


class _RunNightlyLoo(Protocol):
    def __call__(
        self,
        *,
        runner: object,
        session: Session,
        sut: object,
        base_config: object,
        items: Sequence[object],
        suite: str,
        dataset_version: str,
        K: int,
        suite_id: UUID,
        team_id: UUID,
        solution_id: UUID,
    ) -> list[Attribution]: ...


def test_nightly_loo_dispatcher_runs_sweep(
    session: Session,
    sweep_fixtures: object,
) -> None:
    modes = import_module("beacon_runner.harness.modes")
    run_nightly_loo = cast("_RunNightlyLoo", modes.run_nightly_loo)

    fixtures = cast("_SweepFixtures", sweep_fixtures)
    attributions = run_nightly_loo(
        runner=fixtures.runner,
        session=session,
        sut=fixtures.sut,
        base_config=fixtures.base_config,
        items=fixtures.items,
        suite="dummy-suite",
        dataset_version="v1",
        K=2,
        suite_id=fixtures.suite_id,
        team_id=fixtures.team_id,
        solution_id=fixtures.solution_id,
    )

    assert len(attributions) == 2
    assert {attr.layer_name for attr in attributions} == {"ontology", "retry_loop"}
    for attr in attributions:
        assert attr.methodology == "LOO"
        assert attr.bh_adjusted_p is not None
