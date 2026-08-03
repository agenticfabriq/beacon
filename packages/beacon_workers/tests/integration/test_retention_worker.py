"""Retention worker tick enforces full-trace TTL."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
from beacon_storage.models.suites import Suite
from beacon_storage.models.tenancy import Team, User
from beacon_storage.object_storage.local import LocalFsStorage
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.traces import TraceRepo
from beacon_storage.repository.verdicts import VerdictRepo
from beacon_workers.retention.worker import RetentionWorker
from sqlalchemy import text

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_retention_summarizes_old_traces(engine: Engine, tmp_path: Path) -> None:
    from beacon_storage.db import make_session_factory

    factory = make_session_factory(engine)
    storage = LocalFsStorage(root=tmp_path)

    with factory() as session:
        team, user, suite = _ctx(session)
        old_key = f"team={team.id}/old.json"
        new_key = f"team={team.id}/new.json"
        storage.put(old_key, io.BytesIO(b'{"big": "old-trace"}'))
        storage.put(new_key, io.BytesIO(b'{"big": "new-trace"}'))
        old_id = _trace_with_result(
            session=session,
            team=team,
            user=user,
            suite=suite,
            object_storage_uri=old_key,
            pass_idx=0,
        )
        new_id = _trace_with_result(
            session=session,
            team=team,
            user=user,
            suite=suite,
            object_storage_uri=new_key,
            pass_idx=1,
        )
        VerdictRepo(session).create(
            team_id=team.id,
            result_id=_result_id_for_trace(session, old_id),
            grader="g",
            grader_version="v1",
            criterion="correctness",
            bool_value=True,
            value=1.0,
            justification="ok",
            raw_output={"ok": True},
            answer_hash="h",
            confidence=0.95,
        )
        session.execute(
            text("UPDATE traces SET created_at = :created_at WHERE id = :trace_id"),
            {
                "created_at": datetime.now(UTC) - timedelta(days=100),
                "trace_id": old_id,
            },
        )
        session.commit()

    worker = RetentionWorker(
        factory=factory,
        storage=storage,
        retention_days=90,
        batch_size=100,
    )
    worker.tick()

    with factory() as session:
        old_after = TraceRepo(session).get(old_id)
        new_after = TraceRepo(session).get(new_id)

        assert old_after is not None
        assert old_after.summary is not None
        metrics = cast("dict[str, object]", old_after.summary["metrics"])
        assert old_after.summary["pass"] is True
        assert metrics["latency_ms"] == 234
        assert old_after.object_storage_uri is None

        assert new_after is not None
        assert new_after.summary is None
        assert new_after.object_storage_uri == new_key

    assert not storage.exists(old_key)
    assert storage.exists(new_key)


def test_retention_idempotent(engine: Engine, tmp_path: Path) -> None:
    from beacon_storage.db import make_session_factory

    factory = make_session_factory(engine)
    storage = LocalFsStorage(root=tmp_path)

    with factory() as session:
        team, user, suite = _ctx(session)
        key = f"team={team.id}/old.json"
        storage.put(key, io.BytesIO(b"{}"))
        trace_id = _trace_with_result(
            session=session,
            team=team,
            user=user,
            suite=suite,
            object_storage_uri=key,
            pass_idx=0,
        )
        session.execute(
            text("UPDATE traces SET created_at = :created_at WHERE id = :trace_id"),
            {
                "created_at": datetime.now(UTC) - timedelta(days=100),
                "trace_id": trace_id,
            },
        )
        session.commit()

    worker = RetentionWorker(
        factory=factory,
        storage=storage,
        retention_days=90,
        batch_size=100,
    )
    worker.tick()
    with factory() as session:
        row = TraceRepo(session).get(trace_id)
        assert row is not None
        assert row.summary is not None
        first_summarized = row.summary["summarized_at"]

    worker.tick()
    with factory() as session:
        row = TraceRepo(session).get(trace_id)
        assert row is not None
        assert row.summary is not None
        assert row.summary["summarized_at"] == first_summarized


def _ctx(session: Session) -> tuple[Team, User, Suite]:
    team = Team(name="retention-team")
    user = User(email="retention@example.com", name="Retention")
    session.add_all([team, user])
    session.flush()
    suite = Suite(
        team_id=team.id,
        name="retention",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )
    session.add(suite)
    session.flush()
    return team, user, suite


def _trace_with_result(
    *,
    session: Session,
    team: Team,
    user: User,
    suite: Suite,
    object_storage_uri: str,
    pass_idx: int,
) -> UUID:
    solution = SolutionRepo(session).create(
        team_id=team.id,
        solution_id=f"retention-sut-{pass_idx}",
        version="0.1.0",
        owner_team=team.id,
        summary="retention",
        supported_modes=["EVAL"],
        layers=[],
        created_by=user.id,
    )
    run = RunRepo(session).create(
        team_id=team.id,
        suite_id=suite.id,
        solution_id=solution.id,
        suite="retention",
        dataset_version="v0",
        mode=HarnessMode.EVAL,
        pass_idx=pass_idx,
        config={},
        created_by=user.id,
    )
    result = ResultRepo(session).create(
        team_id=team.id,
        run_id=run.id,
        item_id=f"item-{pass_idx}",
        attempt_idx=0,
        output={"answer": "42"},
        output_kind="answer",
        tokens_input=150,
        tokens_output=84,
        runtime_ms=234,
        status=ResultStatus.COMPLETED,
        outcome=VerdictOutcome.PASS,
        error=None,
    )
    trace = TraceRepo(session).create(
        team_id=team.id,
        result_id=result.id,
        step_tree={"name": "root", "children": []},
        object_storage_uri=object_storage_uri,
    )
    return trace.id


def _result_id_for_trace(session: Session, trace_id: UUID) -> UUID:
    trace = TraceRepo(session).get(trace_id)
    assert trace is not None
    return trace.result_id
