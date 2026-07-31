"""Seed curated `human_verified` items for anti-Goodhart sampling.

The validator needs eval items whose question text does **not** trivially
correspond to the gold answer so manual anti-Goodhart sampling can produce a
meaningful "no leakage" verdict per `docs/VALIDATE.md`. This script loads the
JSONL fixtures under `validation-fixtures/anti_goodhart/` into the database
as `human_verified` items with provenance events recording the seed action.

Items are scoped to the `acme` team (the demo seed creates it) so they are
visible in the live API/dashboard under that team's review queue history.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_storage.db import make_engine, make_session_factory, session_scope
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.provenance import ActorType
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.provenance import ProvenanceRepo
from beacon_storage.repository.teams import TeamRepo

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "validation-fixtures" / "anti_goodhart"
DATASET_VERSION = "validation-anti-goodhart-v1"
SEED_REASON = "validation anti-goodhart sampling fixture"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Return parsed JSONL rows from ``path``."""
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _ensure_seed_user(session: Session) -> UUID:
    """Return a stable validation seed user id."""
    user = UserService(session).upsert_from_oidc(
        OidcClaims(
            subject="validation-seed",
            email="validation-seed@example.com",
            name="Validation Seed",
        )
    )
    return user.id


def _ensure_acme_team_id(session: Session) -> UUID:
    """Return the ACME demo team id, requiring `beacon demo seed` first."""
    team = TeamRepo(session).get_by_name("acme")
    if team is None:
        raise RuntimeError(
            "ACME team not found. Run `beacon demo seed` first to bootstrap "
            "teams/projects before seeding anti-Goodhart fixtures."
        )
    return team.id


def _suite_for_fixture(filename: str) -> str:
    """Return the canonical suite name for a fixture filename."""
    return filename.removesuffix(".jsonl")


def seed_anti_goodhart_fixtures(session: Session) -> dict[str, int]:
    """Load every JSONL under FIXTURES_DIR and return per-suite insertion counts."""
    team_id = _ensure_acme_team_id(session)
    seed_user_id = _ensure_seed_user(session)
    items_repo = EvalItemRepo(session)
    prov_repo = ProvenanceRepo(session)

    counts: dict[str, int] = {}
    for fixture_path in sorted(FIXTURES_DIR.glob("*.jsonl")):
        suite = _suite_for_fixture(fixture_path.name)
        rows = _load_jsonl(fixture_path)
        inserted_for_suite = 0
        for row in rows:
            item, created = items_repo.upsert_by_question_hash(
                tier=EvalItemTier.HUMAN_VERIFIED,
                suite=suite,
                team_id=team_id,
                dataset_version=DATASET_VERSION,
                question_hash=row["question_hash"],
                item_input=row["item_input"],
                gold_answer=row.get("gold_answer"),
                item_metadata=row.get("item_metadata"),
                evidence=row.get("evidence"),
                created_by=seed_user_id,
            )
            if not created:
                continue
            prov_repo.append(
                item_id=item.item_id,
                team_id=team_id,
                prior_tier=None,
                new_tier=EvalItemTier.HUMAN_VERIFIED,
                actor_type=ActorType.SYSTEM,
                actor_id="scripts/seed_anti_goodhart_fixtures.py",
                created_by=seed_user_id,
                reason=SEED_REASON,
                evidence={"fixture": fixture_path.name},
            )
            inserted_for_suite += 1
        counts[suite] = inserted_for_suite
    return counts


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="DB URL (default: $DATABASE_URL).",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url required")

    engine = make_engine(args.database_url)
    try:
        factory = make_session_factory(engine)
        with session_scope(factory) as session:
            counts = seed_anti_goodhart_fixtures(session)
    finally:
        engine.dispose()

    print(json.dumps({"seeded": counts, "total": sum(counts.values())}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
