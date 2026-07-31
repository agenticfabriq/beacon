"""Seed production-like traces for validation worker pipelines."""

from __future__ import annotations

import argparse
import json
import os

from beacon_runner.dummy_sut import DummySUT
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.ids import uuid7_str
from beacon_storage.repository.production_traces import ProductionTraceRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo


def _database_url(args: argparse.Namespace) -> str:
    return str(
        args.database_url
        or os.environ.get("DATABASE_URL")
        or "postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed Beacon production traces.")
    parser.add_argument("--solution", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--database-url", default=None)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.count < 1:
        raise SystemExit("--count must be >= 1")

    engine = make_engine(_database_url(args))
    try:
        factory = make_session_factory(engine)
        with factory() as session:
            suffix = uuid7_str()[:8]
            user = UserRepo(session).create(
                email=f"validation-traces-{suffix}@example.com",
                name="Validation Trace Seeder",
            )
            team = TeamRepo(session).create(name=f"validation-traces-{suffix}")
            project = ProjectRepo(session).create(
                team_id=team.id,
                name=f"validation-traces-{suffix}",
                created_by=user.id,
            )
            SolutionRepo(session).create(
                team_id=team.id,
                solution_id=args.solution,
                version=DummySUT.VERSION if args.solution == "dummy" else "validation-v1",
                owner_team=team.id,
                summary="Validation trace seed solution",
                supported_modes=["EVAL", "TRACE_PROMOTION"],
                layers=[],
                created_by=user.id,
            )

            repo = ProductionTraceRepo(session)
            trace_ids: list[str] = []
            for idx in range(args.count):
                trace = repo.create(
                    team_id=team.id,
                    project_id=project.id,
                    solution_id=args.solution,
                    api_key_id=None,
                    item_input={"question": f"validation question {idx + 1}"},
                    item_output={"answer": f"validation answer {idx + 1}"},
                    trace_payload={
                        "steps": [
                            {
                                "name": "validation_seed",
                                "level": "workflow",
                                "status": "COMPLETED",
                            }
                        ]
                    },
                    trace_metadata={
                        "source": "validation_seed_traces",
                        "suggested_suite": "ad_hoc",
                        "contains_leakage": idx == 0,
                    },
                    is_eval_candidate=True,
                    derived_trace_id=None,
                )
                trace_ids.append(str(trace.id))

            session.commit()
            print(
                json.dumps(
                    {
                        "solution": args.solution,
                        "count": args.count,
                        "team_id": str(team.id),
                        "project_id": str(project.id),
                        "trace_ids": trace_ids,
                    },
                    sort_keys=True,
                )
            )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
