"""GET /v1/teams/{team_id}/anti-goodhart returns team-scoped findings."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_iam.auth.api_key import generate_api_key, hash_api_key
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_storage.models.antigoodhart import AntigoodhartKind, AntigoodhartSeverity
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.antigoodhart import AntigoodhartRepo
from beacon_storage.repository.api_keys import ApiKeyRepo
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.teams import TeamRepo

if TYPE_CHECKING:
    from uuid import UUID

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _make_key(session: Session, email: str, name: str) -> tuple[UUID, str]:
    user = UserService(session).upsert_from_oidc(OidcClaims(subject=email, email=email, name=name))
    key = generate_api_key(prefix="bcn_test")
    ApiKeyRepo(session).create(user_id=user.id, key_hash=hash_api_key(key), label="test")
    session.commit()
    return user.id, key


@pytest.fixture
def anti_goodhart_seed(session: Session) -> dict[str, object]:
    team = TeamRepo(session).create(name="anti-goodhart-api")
    member_id, member_key = _make_key(session, "agh-member@o.com", "AGH Member")
    _outsider_id, outsider_key = _make_key(session, "agh-outsider@o.com", "AGH Outsider")
    MembershipRepo(session).grant(
        user_id=member_id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team.id,
        role=Role.TEAM_MEMBER,
    )
    repo = AntigoodhartRepo(session)
    scan_id = uuid4()
    suite_id = uuid4()
    item_id = uuid4()
    repo.record(
        scan_id=scan_id,
        item_id=item_id,
        team_id=team.id,
        suite_id=suite_id,
        kind=AntigoodhartKind.EVIDENCE_LEAK,
        severity=AntigoodhartSeverity.HIGH,
        description="gold answer appears in evidence",
        evidence={"path": "evidence"},
    )
    repo.record(
        scan_id=scan_id,
        item_id=None,
        team_id=team.id,
        suite_id=suite_id,
        kind=AntigoodhartKind.DISTRIBUTION_SKEW,
        severity=AntigoodhartSeverity.LOW,
        description="team owns most items",
        evidence={"team_share": 1.0},
    )
    session.commit()
    return {
        "team_id": team.id,
        "member_key": member_key,
        "outsider_key": outsider_key,
        "item_id": item_id,
        "suite_id": suite_id,
    }


def test_returns_findings_for_team_member(
    api_client: TestClient,
    anti_goodhart_seed: dict[str, object],
) -> None:
    response = api_client.get(
        f"/v1/teams/{anti_goodhart_seed['team_id']}/anti-goodhart",
        headers={"X-API-Key": str(anti_goodhart_seed["member_key"])},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["findings"][0]["kind"] == "evidence_leak"
    assert body["findings"][0]["severity"] == "high"
    assert body["findings"][0]["eval_item_id"] == str(anti_goodhart_seed["item_id"])
    assert body["findings"][0]["suite_id"] == str(anti_goodhart_seed["suite_id"])
    assert body["findings"][0]["detail"] == "gold answer appears in evidence"
    assert isinstance(body["findings"][0]["created_at_ts"], int)


def test_non_member_sees_empty_findings(
    api_client: TestClient,
    anti_goodhart_seed: dict[str, object],
) -> None:
    response = api_client.get(
        f"/v1/teams/{anti_goodhart_seed['team_id']}/anti-goodhart",
        headers={"X-API-Key": str(anti_goodhart_seed["outsider_key"])},
    )

    assert response.status_code == 200, response.text
    assert response.json()["findings"] == []


def test_filters_by_kind(
    api_client: TestClient,
    anti_goodhart_seed: dict[str, object],
) -> None:
    response = api_client.get(
        f"/v1/teams/{anti_goodhart_seed['team_id']}/anti-goodhart?kind=evidence_leak",
        headers={"X-API-Key": str(anti_goodhart_seed["member_key"])},
    )

    assert response.status_code == 200, response.text
    findings = response.json()["findings"]
    assert findings
    assert all(finding["kind"] == "evidence_leak" for finding in findings)


def test_orders_by_severity(
    api_client: TestClient,
    anti_goodhart_seed: dict[str, object],
) -> None:
    response = api_client.get(
        f"/v1/teams/{anti_goodhart_seed['team_id']}/anti-goodhart",
        headers={"X-API-Key": str(anti_goodhart_seed["member_key"])},
    )

    assert response.status_code == 200, response.text
    assert [finding["severity"] for finding in response.json()["findings"]] == [
        "high",
        "low",
    ]
