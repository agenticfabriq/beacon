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
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def issue_operator_key(session: Session, *, email: str, name: str, team_name: str) -> str:
    """Create the user and team if absent, grant admin, mint a key, return it.

    Idempotent in the user, the team and the grant -- it is run by hand and may
    well be run twice -- and deliberately NOT idempotent in the key. Only the
    hash is stored, so a re-run cannot reprint the previous credential; it
    mints a new one instead of pretending to recover the old.
    """
    # One positional dataclass, not keywords. The subject is synthetic: no IdP
    # issued this user, and the column wants something stable and unique.
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

    # The deployment sets BEACON_API_KEY_PREFIX; the library default is
    # `bcn_dev`, which would quietly label production keys as development ones.
    key = generate_api_key(prefix=os.environ.get("BEACON_API_KEY_PREFIX", "bcn_dev"))
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
    print("Printed once. Only the hash is stored; a re-run mints a new key.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
