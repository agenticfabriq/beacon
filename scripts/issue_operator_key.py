"""Mint the one API key that bootstraps a fresh beacon deployment.

Nothing in a public-URL, API-keys-only deployment can issue a first key.
``POST /v1/me/api-keys`` and the team roster both require an authenticated
session; the OIDC routes 503 because ``oidc_issuer`` is unset by design;
``/v1/auth/password/login`` 401s because every user has a NULL
``password_hash``; and ``beacon demo seed`` mints only for the demo accounts
and seeds demo teams alongside the migrated corpora. This is the seam that
starts the chain, run once on the instance with DATABASE_URL set.

It is deliberately not an API surface. It mints exactly one credential and
prints it once, because a key readable twice is a key stored somewhere it
should not be.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING

from beacon_iam.auth.api_key import generate_api_key, hash_api_key
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_storage.db import make_session_factory, session_scope
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.api_keys import ApiKeyRepo
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class _KeyPrefix(BaseSettings):
    """Just the prefix, on ApiConfig's env contract and none of its demands.

    Not `ApiConfig` itself: that declares `database_url` REQUIRED, and this
    script accepts the URL as `--database-url`. Constructing ApiConfig here
    creates the user, the team and the grant, then raises "database_url is
    required" at an operator who has just supplied one -- and `session_scope`
    rolls all of it back with no key minted. The test suite could not catch it
    either: conftest exports DATABASE_URL for every test taking a session,
    which is why one test here strips it.

    `env_file` resolves against the PROCESS CWD, exactly as ApiConfig's does.
    Run from a directory with no `.env` -- a systemd unit, a container root --
    and the prefix silently falls back to `bcn_dev`, because a mislabelled key
    authenticates perfectly well. main() prints the prefix it used for that
    reason.
    """

    api_key_prefix: str = "bcn_dev"

    model_config = SettingsConfigDict(
        env_prefix="BEACON_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def issue_operator_key(session: Session, *, email: str, name: str, team_name: str) -> str:
    """Create the user and team if absent, grant admin, mint a key, return it.

    Idempotent in the user, the team and the grant -- it is run by hand and may
    well be run twice -- and deliberately NOT idempotent in the key. Only the
    hash is stored, so a re-run cannot reprint the previous credential; it
    mints a new one instead of pretending to recover the old.
    """
    # `upsert_from_oidc` links by subject, then by email but ONLY when the
    # existing row has no subject. Our subject is synthetic (`bootstrap:`), so
    # an email that has already signed in through an IdP matches neither
    # branch and falls through to `create`, which violates the unique
    # constraint on users.email and surfaces as a raw psycopg IntegrityError.
    # Nothing is corrupted -- the transaction rolls back -- but the operator is
    # handed a traceback instead of the one fact they need.
    users = UserRepo(session)
    clash = users.get_by_email(email)
    if clash is not None and clash.oidc_subject not in (None, f"bootstrap:{email}"):
        raise SystemExit(
            f"{email} already exists with oidc_subject {clash.oidc_subject!r}, which "
            f"this script did not create. If an identity provider owns it, that account "
            f"can already sign in; if it came from `beacon demo seed`, it is demo data. "
            f"Bootstrap a different address."
        )

    # One positional dataclass, not keywords.
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject=f"bootstrap:{email}", email=email, name=name)
    )

    teams = TeamRepo(session)
    team = teams.get_by_name(team_name) or teams.create(name=team_name)

    # Membership lives on its own repo, and `grant` upserts on conflict, which
    # is what makes a second run safe. Without this the key authenticates and
    # then sees nothing -- a bootstrap that looks successful until used.
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team.id,
        role=Role.TEAM_ADMIN,
    )

    # Through settings, not os.environ, so `.env` is honoured -- that file is
    # how README.md and .env.example tell an operator to set this, and it is
    # what the running API reads. A bare os.environ lookup mints `bcn_dev_...`
    # for an operator who configured the prefix the documented way, while every
    # API-minted key on the same instance carries the configured one.
    key = generate_api_key(prefix=_KeyPrefix().api_key_prefix)
    ApiKeyRepo(session).create(
        user_id=user.id, key_hash=hash_api_key(key), label="operator-bootstrap"
    )
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--team", default="beacon-ops")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url is required")

    factory = make_session_factory(create_engine(args.database_url))
    with session_scope(factory) as session:
        key = issue_operator_key(session, email=args.email, name=args.name, team_name=args.team)

    # The key alone on stdout, so it can be piped; everything else on stderr.
    print(key)
    # The prefix is echoed because getting it wrong is otherwise invisible: a
    # mislabelled key authenticates normally, and the only way to notice is to
    # compare it against a key minted through the API.
    print(
        f"Prefix {_KeyPrefix().api_key_prefix!r} (BEACON_API_KEY_PREFIX, from env or ./.env). "
        "Printed once; only the hash is stored, so a re-run mints a new key.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
