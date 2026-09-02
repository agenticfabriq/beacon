"""Minting the one credential that bootstraps a fresh deployment.

Lives here, not at ``BEACON/tests/``, for the ``session`` fixture: it is
defined per package in ``packages/*/tests/conftest.py`` and there is no root
one, so anywhere else this errors with "fixture 'session' not found" before a
single assertion runs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))

from issue_operator_key import issue_operator_key  # noqa: E402

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_it_honours_the_configured_key_prefix(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DISTINCTIVE prefix, because the obvious assertion cannot fail.

    ``generate_api_key`` defaults to ``bcn_dev``, which itself starts with
    ``bcn_`` -- so asserting ``startswith("bcn_")`` passes whether or not
    BEACON_API_KEY_PREFIX was read at all. The deployment sets that variable,
    and this is the only test that would notice if it stopped being consulted.
    """
    monkeypatch.setenv("BEACON_API_KEY_PREFIX", "opskey_")

    key = issue_operator_key(session, email="ops@example.com", name="Ops", team_name="beacon-ops")

    assert key.startswith("opskey_")
    assert len(key) > 20


def test_the_prefix_is_read_from_dotenv_not_just_the_environment(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`.env` is how the docs say to set this, and only ApiConfig reads it.

    The sibling test sets the variable in the process environment, where a bare
    `os.environ.get` and `ApiConfig` behave identically -- so it cannot tell
    which one the script uses. `.env` separates them: `ApiConfig` declares it
    an env source, `os.environ` never sees it. An operator who configures the
    prefix the documented way would otherwise get `bcn_dev_...` keys while
    every API-minted key on the same box carried the configured one.
    """
    monkeypatch.delenv("BEACON_API_KEY_PREFIX", raising=False)
    (tmp_path / ".env").write_text("BEACON_API_KEY_PREFIX=fromdotenv_\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    key = issue_operator_key(session, email="ops@example.com", name="Ops", team_name="beacon-ops")

    assert key.startswith("fromdotenv_")


def test_it_mints_without_DATABASE_URL_in_the_environment(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The script takes --database-url; the environment need not carry it.

    Reading the prefix through `ApiConfig` made this fail, because ApiConfig
    declares `database_url` REQUIRED -- so an operator passing --database-url
    with nothing exported got the user, the team and the grant created and
    then "database_url is required", rolled back, no key. Every other test
    here passes with that bug present: conftest exports DATABASE_URL for any
    test taking a session, which is exactly why this one strips it.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BEACON_DATABASE_URL", raising=False)
    monkeypatch.delenv("BEACON_API_KEY_PREFIX", raising=False)
    monkeypatch.chdir(tmp_path)

    key = issue_operator_key(session, email="ops@example.com", name="Ops", team_name="beacon-ops")

    assert key.startswith("bcn_dev")


def test_the_key_is_stored_hashed_and_never_in_plaintext(session: Session) -> None:
    """A key recoverable from the database is a key that leaked with a backup."""
    from beacon_storage.models.tenancy import ApiKey

    key = issue_operator_key(session, email="ops@example.com", name="Ops", team_name="beacon-ops")

    rows = session.query(ApiKey).all()
    assert len(rows) == 1
    assert key not in rows[0].key_hash
    assert rows[0].revoked_at is None


def test_the_printed_key_is_the_one_authentication_will_accept(session: Session) -> None:
    """The whole point of the artifact, and the one thing absence-of-plaintext does not show.

    `key not in key_hash` holds for ANY hash of anything -- hash the key plus a
    stray character and the stored digest still contains no plaintext, the row
    still exists, and the script still prints a key. It would simply never
    authenticate, and the operator would find out on a fresh deployment with no
    other way in.

    Resolved the way `deps.py` resolves it: hash the plaintext, look it up.
    """
    from beacon_iam.auth.api_key import hash_api_key
    from beacon_storage.repository.api_keys import ApiKeyRepo

    key = issue_operator_key(session, email="ops@example.com", name="Ops", team_name="beacon-ops")

    found = ApiKeyRepo(session).get_by_hash(hash_api_key(key))

    assert found is not None
    assert found.label == "operator-bootstrap"


def test_running_it_twice_does_not_create_a_second_user_or_team(session: Session) -> None:
    """Re-running a bootstrap must be safe; it is run by hand, possibly twice."""
    from beacon_storage.models.tenancy import Team, User

    issue_operator_key(session, email="ops@example.com", name="Ops", team_name="beacon-ops")
    issue_operator_key(session, email="ops@example.com", name="Ops", team_name="beacon-ops")

    assert session.query(User).filter_by(email="ops@example.com").count() == 1
    assert session.query(Team).filter_by(name="beacon-ops").count() == 1


def test_each_run_mints_a_distinct_key(session: Session) -> None:
    """The old key is not reprinted, so a second run must yield a new one."""
    first = issue_operator_key(session, email="ops@example.com", name="Ops", team_name="beacon-ops")
    second = issue_operator_key(
        session, email="ops@example.com", name="Ops", team_name="beacon-ops"
    )

    assert first != second


def test_an_email_already_linked_to_an_idp_fails_with_an_explanation(session: Session) -> None:
    """Not a traceback, because the operator has to decide what to do next.

    `upsert_from_oidc` links by subject, then by email ONLY when the existing
    row has no subject. This script's subject is synthetic, so an account that
    has already signed in through an IdP matches neither branch and falls
    through to `create` -- violating the unique constraint on users.email and
    surfacing as a raw psycopg IntegrityError. The transaction rolls back so
    nothing is corrupted; the operator just gets no idea why.
    """
    from beacon_storage.repository.users import UserRepo

    UserRepo(session).create(
        email="ops@example.com", name="Ops", oidc_subject="https://idp.example/sub/42"
    )
    session.flush()

    with pytest.raises(SystemExit) as excinfo:
        issue_operator_key(session, email="ops@example.com", name="Ops", team_name="beacon-ops")

    message = str(excinfo.value)
    assert "ops@example.com" in message
    assert "https://idp.example/sub/42" in message


def test_the_operator_can_administer_the_team(session: Session) -> None:
    """The key is useless without the grant that makes it an admin.

    Minting a credential attached to no membership yields a login that can see
    nothing -- which looks like a working bootstrap right up until someone
    tries to use it, and success criteria 2 and 3 both need team admin.
    """
    from beacon_storage.models.tenancy import Membership, Role, ScopeKind, Team, User

    issue_operator_key(session, email="ops@example.com", name="Ops", team_name="beacon-ops")

    user = session.query(User).filter_by(email="ops@example.com").one()
    team = session.query(Team).filter_by(name="beacon-ops").one()
    grant = session.query(Membership).filter_by(user_id=user.id, scope_id=team.id).one()

    # `==`, not `is`. Membership declares `Mapped[Role]` over a `String(40)`
    # column, so SQLAlchemy hands back the raw string on load and mypy is the
    # only thing that thinks it is a Role. `Role` is a StrEnum, so equality
    # holds and every caller in the repo uses it; identity silently would not.
    assert grant.role == Role.TEAM_ADMIN
    assert grant.scope_kind == ScopeKind.TEAM
