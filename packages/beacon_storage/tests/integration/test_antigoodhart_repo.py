"""AntigoodhartRepo record and query semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_storage.models.antigoodhart import (
    AntigoodhartFinding,
    AntigoodhartKind,
    AntigoodhartSeverity,
)
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.antigoodhart import AntigoodhartRepo
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from sqlalchemy import select, text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _use_app_role(session: Session) -> None:
    session.execute(text("SET LOCAL ROLE beacon_app"))


def test_record_and_list_by_scan(session: Session) -> None:
    repo = AntigoodhartRepo(session)
    scan_id = uuid4()
    item_id = uuid4()

    repo.record(
        scan_id=scan_id,
        item_id=item_id,
        team_id=uuid4(),
        suite_id=uuid4(),
        kind=AntigoodhartKind.SQL_IN_QUESTION,
        severity=AntigoodhartSeverity.HIGH,
        description="sql overlap",
        evidence={"x": 1},
    )
    session.commit()
    findings = repo.list_for_scan(scan_id)

    assert len(findings) == 1
    assert findings[0].item_id == item_id


def test_latest_scan_id_for_suite(session: Session) -> None:
    repo = AntigoodhartRepo(session)
    team_id = uuid4()
    suite_id = uuid4()
    first_scan_id = uuid4()
    second_scan_id = uuid4()

    repo.record(
        scan_id=first_scan_id,
        item_id=uuid4(),
        team_id=team_id,
        suite_id=suite_id,
        kind=AntigoodhartKind.EVIDENCE_LEAK,
        severity=AntigoodhartSeverity.LOW,
        description="first",
        evidence={},
    )
    repo.record(
        scan_id=second_scan_id,
        item_id=uuid4(),
        team_id=team_id,
        suite_id=suite_id,
        kind=AntigoodhartKind.EVIDENCE_LEAK,
        severity=AntigoodhartSeverity.LOW,
        description="second",
        evidence={},
    )
    session.commit()

    assert repo.latest_scan_id(team_id=team_id, suite_id=suite_id) == second_scan_id


def test_findings_rls_filters_by_team(engine: Engine) -> None:
    from beacon_storage.db import make_session_factory

    factory = make_session_factory(engine)
    scan_id = uuid4()

    with factory() as superuser:
        alice = UserRepo(superuser).create(email="ag-rls-a@example.com", name="A")
        bob = UserRepo(superuser).create(email="ag-rls-b@example.com", name="B")
        team_a = TeamRepo(superuser).create(name="ag-rls-a")
        team_b = TeamRepo(superuser).create(name="ag-rls-b")
        MembershipRepo(superuser).grant(
            user_id=alice.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_a.id,
            role=Role.TEAM_MEMBER,
        )
        MembershipRepo(superuser).grant(
            user_id=bob.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_b.id,
            role=Role.TEAM_MEMBER,
        )
        repo = AntigoodhartRepo(superuser)
        repo.record(
            scan_id=scan_id,
            item_id=uuid4(),
            team_id=team_a.id,
            suite_id=None,
            kind=AntigoodhartKind.EVIDENCE_LEAK,
            severity=AntigoodhartSeverity.HIGH,
            description="a",
            evidence={},
        )
        repo.record(
            scan_id=scan_id,
            item_id=uuid4(),
            team_id=team_b.id,
            suite_id=None,
            kind=AntigoodhartKind.EVIDENCE_LEAK,
            severity=AntigoodhartSeverity.HIGH,
            description="b",
            evidence={},
        )
        superuser.commit()
        alice_id = alice.id
        bob_id = bob.id
        team_a_id = team_a.id

    with factory() as session:
        _use_app_role(session)
        set_current_user(session, bob_id)
        visible = session.scalars(
            select(AntigoodhartFinding).where(AntigoodhartFinding.team_id == team_a_id)
        ).all()

    assert visible == []

    with factory() as session:
        _use_app_role(session)
        set_current_user(session, alice_id)
        visible = session.scalars(
            select(AntigoodhartFinding).where(AntigoodhartFinding.team_id == team_a_id)
        ).all()

    assert len(visible) == 1
