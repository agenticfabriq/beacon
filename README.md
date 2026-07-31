# Beacon

Beacon is an evaluation framework for database-grounded data agents. The v2
implementation includes multi-team tenancy, a run harness, graders, statistical
attribution, an eval-item registry, production-trace ingestion, background
workers, benchmark adapters, a REST API, a CLI, and a Streamlit dashboard.

The main idea is to evaluate not only whether an agent can answer a task, but
also how reliably it answers across attempts, which system layers contribute to
that reliability, and whether promoted eval data has trustworthy provenance.

## Repository layout

- `packages/beacon_storage`: SQLAlchemy models, repositories, RLS helpers, and Alembic migrations.
- `packages/beacon_iam`: users, memberships, roles, permissions, OIDC/API-key auth.
- `packages/beacon_runner`: SUT protocol, harness modes, run orchestration, DummySUT.
- `packages/beacon_graders`: grader protocol and shipped graders.
- `packages/beacon_ablation`: attribution statistics and leave-one-out analysis.
- `packages/beacon_registry`: eval-item lifecycle and provenance services.
- `packages/beacon_workers`: promotion, convergence, anti-Goodhart, and retention workers.
- `packages/beacon_sdk`: async and sync client APIs for trace ingestion.
- `packages/beacon_benchmarks`: benchmark adapters and regression fixtures.
- `packages/beacon_ui`: FastAPI routes, CLI, and Streamlit dashboard.
- `scripts`: validation utilities such as attribution sweep and trace seeding.

## Requirements

- Python 3.11 or newer.
- `uv` for the project-local Python environment.
- Docker or a Docker-compatible runtime for local Postgres.
- `make`.

Do not install dependencies globally. Use the repo-local `uv` environment.

## Quick start

Install all workspace packages:

```bash
uv sync --all-packages
```

Start Postgres and apply migrations:

```bash
make db-up
make migrate
```

The default local database URL is:

```text
postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon
```

If port `5432` is already in use, choose another port and keep it consistent:

```bash
POSTGRES_PORT=55432 make db-up
POSTGRES_PORT=55432 make migrate
```

## Run the demo UI

Seed the demo data:

```bash
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon uv run beacon demo seed
```

The seed command prints demo API keys. Use the `alice@example.com` key for the
ACME project demo, or `carol@example.com` for the Globex project demo.

Start the API:

```bash
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon \
BEACON_DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon \
uv run uvicorn beacon_ui.api.app:app --reload --port 8000
```

In a second terminal, start the dashboard:

```bash
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon \
uv run streamlit run packages/beacon_ui/src/beacon_ui/dashboard/app.py
```

Open `http://localhost:8501`, enter API base `http://localhost:8000`, and paste
one of the demo API keys printed by `beacon demo seed`.

For a single-command local demo path, the Makefile also provides:

```bash
make demo
```

## Background workers

Run workers in separate terminals when validating the live worker pipeline:

```bash
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon uv run beacon-worker-promotion
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon uv run beacon-worker-convergence
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon uv run beacon-worker-antigoodhart
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon uv run beacon-worker-retention
```

## Common development commands

```bash
make lint
make typecheck
make test
```

Run the full coverage check:

```bash
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon uv run pytest --cov=packages
```

Run the load-bearing DummySUT attribution sweep:

```bash
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon \
uv run python scripts/sweep.py --sut dummy --suite test_suite --pass-num 5 --tasks 50 --mode NIGHTLY_LOO
```

Seed production-like traces for worker validation:

```bash
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon \
uv run python scripts/seed_traces.py --solution dummy --count 100
```

Stop local Postgres:

```bash
make db-down
```

## Notes

- The test suite uses Postgres-backed fixtures for integration coverage.
- Some tests intentionally skip external provider round trips unless credentials are configured.
- Multi-team tenancy is dormant by default: with only `DATABASE_URL` set, Beacon
  runs as a single-tenant local instance (RLS policies short-circuit unless a
  user context is explicitly set), and API-key local mode is first-class.

## License

Apache-2.0 — see [LICENSE](LICENSE).
