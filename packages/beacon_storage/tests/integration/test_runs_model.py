"""Run/Result/Verdict/Trace model wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_storage.ids import uuid7
from beacon_storage.models.runs import (
    HarnessMode,
    Result,
    ResultStatus,
    Run,
    RunStatus,
    Trace,
    Verdict,
    VerdictOutcome,
)
from beacon_storage.models.solutions import Solution
from beacon_storage.models.suites import Suite
from beacon_storage.models.tenancy import Team, User
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture
def _bootstrap(session: Session) -> tuple[Team, User, Suite, Solution]:
    t = Team(name="runs-team")
    u = User(email="runs@example.com", name="R")
    session.add_all([t, u])
    session.flush()
    p = Suite(
        team_id=t.id,
        name="proj",
        description="",
        method="manual",
        suite_metadata={},
        created_by=u.id,
    )
    session.add(p)
    session.flush()
    s = Solution(
        team_id=t.id,
        solution_id="dummy",
        version="0.2.0",
        owner_team=t.id,
        summary="x",
        supported_modes=["EVAL"],
        layers=[],
        created_by=u.id,
    )
    session.add(s)
    session.flush()
    return t, u, p, s


@pytest.mark.integration
def test_create_run(session: Session, _bootstrap: tuple[Team, User, Suite, Solution]) -> None:
    t, u, p, s = _bootstrap
    r = Run(
        team_id=t.id,
        suite_id=p.id,
        solution_id=s.id,
        suite="dummy_smoke_v1",
        dataset_version="v0",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        parent_sweep_id=None,
        config={"layers_enabled": {}},
        status=RunStatus.PENDING,
        created_by=u.id,
    )
    session.add(r)
    session.commit()
    assert r.id is not None
    assert r.mode == HarnessMode.EVAL


@pytest.mark.integration
def test_unique_run_per_pass(
    session: Session, _bootstrap: tuple[Team, User, Suite, Solution]
) -> None:
    t, u, p, s = _bootstrap
    common = {
        "team_id": t.id,
        "suite_id": p.id,
        "solution_id": s.id,
        "suite": "s",
        "dataset_version": "v0",
        "mode": HarnessMode.EVAL,
        "parent_sweep_id": None,
        "config": {},
        "status": RunStatus.PENDING,
        "created_by": u.id,
    }
    r1 = Run(pass_idx=0, **common)
    session.add(r1)
    session.commit()
    r2 = Run(pass_idx=0, **common)
    session.add(r2)
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.integration
def test_sweep_arms_share_pass_idx_within_one_sweep(
    session: Session, _bootstrap: tuple[Team, User, Suite, Solution]
) -> None:
    """Two ablation arms of one sweep are distinct runs at the same pass_idx."""
    t, u, p, s = _bootstrap
    sweep_id = uuid7()
    common = {
        "team_id": t.id,
        "suite_id": p.id,
        "solution_id": s.id,
        "suite": "s",
        "dataset_version": "v0",
        "mode": HarnessMode.NIGHTLY_LOO,
        "parent_sweep_id": sweep_id,
        "pass_idx": 0,
        "config": {},
        "status": RunStatus.PENDING,
        "created_by": u.id,
    }
    session.add_all(
        [
            Run(sweep_arm="baseline", **common),
            Run(sweep_arm="no_ontology", **common),
            Run(sweep_arm="no_retry_loop", **common),
        ]
    )
    session.commit()

    arms = session.scalars(select(Run.sweep_arm).where(Run.parent_sweep_id == sweep_id))
    assert set(arms) == {"baseline", "no_ontology", "no_retry_loop"}


@pytest.mark.integration
def test_same_arm_twice_in_one_sweep_still_collides(
    session: Session, _bootstrap: tuple[Team, User, Suite, Solution]
) -> None:
    """The arm discriminator widens run identity; it does not disable the guard."""
    t, u, p, s = _bootstrap
    common = {
        "team_id": t.id,
        "suite_id": p.id,
        "solution_id": s.id,
        "suite": "s",
        "dataset_version": "v0",
        "mode": HarnessMode.NIGHTLY_LOO,
        "parent_sweep_id": uuid7(),
        "pass_idx": 0,
        "sweep_arm": "no_ontology",
        "config": {},
        "status": RunStatus.PENDING,
        "created_by": u.id,
    }
    session.add(Run(**common))
    session.commit()
    session.add(Run(**common))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.integration
def test_null_sweep_arm_still_collides_for_standalone_runs(
    session: Session, _bootstrap: tuple[Team, User, Suite, Solution]
) -> None:
    """Existing (arm-less) run identity is unchanged: NULL arms are not distinct."""
    t, u, p, s = _bootstrap
    common = {
        "team_id": t.id,
        "suite_id": p.id,
        "solution_id": s.id,
        "suite": "s",
        "dataset_version": "v0",
        "mode": HarnessMode.EVAL,
        "parent_sweep_id": None,
        "pass_idx": 0,
        "config": {},
        "status": RunStatus.PENDING,
        "created_by": u.id,
    }
    session.add(Run(**common))
    session.commit()
    session.add(Run(**common))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.integration
def test_result_and_verdict_cascade(
    session: Session, _bootstrap: tuple[Team, User, Suite, Solution]
) -> None:
    t, u, p, s = _bootstrap
    r = Run(
        team_id=t.id,
        suite_id=p.id,
        solution_id=s.id,
        suite="s",
        dataset_version="v0",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        parent_sweep_id=None,
        config={},
        status=RunStatus.PENDING,
        created_by=u.id,
    )
    session.add(r)
    session.flush()
    res = Result(
        team_id=t.id,
        run_id=r.id,
        item_id="item-1",
        attempt_idx=0,
        output={"answer": "42"},
        output_kind="answer",
        tokens_input=10,
        tokens_output=5,
        runtime_ms=12,
        status=ResultStatus.COMPLETED,
        outcome=VerdictOutcome.PASS,
    )
    session.add(res)
    session.flush()
    v = Verdict(
        team_id=t.id,
        result_id=res.id,
        grader="dabstep_matcher",
        grader_version="v1",
        criterion="factoid_match",
        bool_value=True,
        value=1.0,
        justification="match",
        raw_output={"a": "b"},
    )
    tr = Trace(
        team_id=t.id,
        result_id=res.id,
        step_tree={"name": "root", "level": "workflow", "children": []},
    )
    session.add_all([v, tr])
    session.commit()
    assert v.result_id == res.id
    assert tr.result_id == res.id
