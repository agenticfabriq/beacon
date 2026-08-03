# Beacon

Beacon tracks how well database-grounded data agents answer questions, and why
one configuration answers better than another.

It is a **tracker, not an executor**. You register a run, execute it on your own
hardware with your own runner, and push the outputs back. Beacon grades them
against gold it holds, keeps the record, and lets you compare runs — including
down to a single question, with your SQL and its result beside the gold's.

The system under test never grades its own work. What you push is `output`; the
verdict is computed here.

## The flow

```
  1. register        POST /v1/projects/{id}/runs
     a run           declare your system, version, model and config
                     -> run_id
                                │
  2. execute         your runner, your hardware, your model.
     elsewhere       beacon is not involved and does not want to be
                                │
  3. push            POST /v1/projects/{id}/runs/{run_id}/results   (per item)
     outputs         POST /v1/projects/{id}/runs/{run_id}/complete
                     beacon grades each one on arrival
                                │
  4. read            GET  .../runs/{run_id}/results         which went which way
     the answer      GET  .../runs/{run_id}/results/{item}  yours beside gold
                     GET  .../projects/{id}/attribution     what each layer did
```

Four things that shape the whole design:

- **A deferral is not a failure.** A system that declines to answer scores
  `DEFER`, not `FAIL`. Otherwise a cautious system and an inaccurate one are the
  same number, and over-deferral — usually the biggest gap between a baseline and
  a ceiling — becomes invisible.
- **An error is not a zero.** An attempt that never ran leaves the denominator
  instead of counting against the model. A rate over no gradeable tasks is
  `None`, not `0.0`.
- **Two metrics, one run.** Exact match and a tolerant reading are reported side
  by side rather than settled by tuning one comparison.
- **A bad run is invalidated, never deleted.** The row and its results stay, with
  who retired it and why. A tracker whose operator can erase inconvenient results
  cannot be cited.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) for the project-local environment
- Docker (or compatible) for local Postgres
- `make`

Don't install dependencies globally — use the repo-local `uv` environment.

## Quick start

```bash
uv sync --all-packages
make db-up
make migrate
```

The default local database URL is:

```text
postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon
```

If `5432` is taken, pick another port and keep it consistent:

```bash
POSTGRES_PORT=55432 make db-up
POSTGRES_PORT=55432 make migrate
```

Seed demo data. It prints API keys — keep one:

```bash
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon \
  uv run beacon demo seed
```

## Launching the UI

Two processes: the API, and the Streamlit dashboard that reads it.

**API** (terminal 1):

```bash
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon \
  uv run uvicorn beacon_ui.api.app:app --reload --port 8000
```

Suites graded by SQL execution need the database that SQL runs against. Without
it, ingestion refuses to grade rather than marking every item `ERROR`:

```bash
BEACON_BENCHMARK_DB_URLS='{"bird_minidev_v2":"postgresql+psycopg://user:pw@localhost:5432/bird_dev"}'
```

**Dashboard** (terminal 2):

```bash
DATABASE_URL=postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon \
  uv run streamlit run packages/beacon_ui/src/beacon_ui/dashboard/app.py
```

Open <http://localhost:8501>. The dashboard reads credentials from the same
places the CLI does — `BEACON_API_KEY`, `BEACON_API_BASE`, and the
`~/.beacon/ctx.json` written by `beacon login` — so it can be pre-authenticated:

```bash
export BEACON_API_KEY=bcn_...          # or: uv run beacon login
export BEACON_API_BASE=http://localhost:8000
```

Otherwise sign in with the API base and a key from `beacon demo seed`.

`make demo` runs `db-up` and `migrate` in one step.

## Command line

`uv run beacon --help` for the full surface. Everything reads `DATABASE_URL`, or
talks to the API using the context from `beacon login`.

| Command | What it does |
|---|---|
| `beacon login` | Authenticate and store credentials in `~/.beacon/ctx.json` |
| `beacon ctx` | Show, set, or clear that context |
| `beacon teams` / `projects` | List and create |
| `beacon suts register` \| `list` | Register a system under test from a Python file |
| `beacon suites create` | Create a suite — the set of questions a run is scored over |
| `beacon eval run` | Register a run for a solution and suite |
| `beacon attribution sweep` \| `show` | Leave-one-out layer sweep, and its latest snapshot |
| `beacon benchmarks list` \| `download` \| `ingest` | Benchmark adapters and their data |
| `beacon gold import` | Import approved gold from the semantic layer |
| `beacon demo seed` | Demo fixtures and API keys |

Beacon does not author gold. Public corpora are ingested; customer gold is
curated, reviewed and given a per-question tolerance in the semantic layer, and
arrives here as an approved export. `beacon gold import` prints what it refused
as well as what it took — a package that imports nothing because everything is
still in review should say so, not look like a no-op.

### Loading results from a file

A runner that writes JSONL rather than calling the API can use:

```bash
BEACON_API_KEY=bcn_... DATABASE_URL=... \
uv run python scripts/load_eval_reports.py \
  --project <uuid> --solution <uuid> --suite <uuid> \
  --manifest reports.json --reports-dir path/to/reports
```

Beacon re-grades everything, so each load doubles as a conformance check between
two independent graders — the disagreement table it prints is the reason to run
it. The manifest supplies model and config per file, because nothing in a report
says which model produced it.

It refuses a report whose cases belong to another corpus. Case ids are not
corpus-qualified: two benchmarks can both number their cases `bird-0`, `bird-1`,
and loading one against the other's gold produces a completely plausible-looking
run over unrelated questions.

## Background workers

```bash
DATABASE_URL=... uv run beacon-worker-retention
```

## Development

```bash
make lint        # ruff check + format --check
make typecheck   # mypy --strict over packages/ and scripts/
make test        # pytest
```

**The test fixtures drop and recreate the `public` schema on whatever database
they are pointed at.** A guard refuses any database whose name does not end in
`_test` or `_ci`, and `make test` targets `beacon_test`. Do not override it to
point at a database whose contents you want to keep.

Run the attribution sweep end to end against the built-in DummySUT:

```bash
DATABASE_URL=... uv run python scripts/sweep.py \
  --sut dummy --suite test_suite --pass-num 5 --tasks 50 --mode NIGHTLY_LOO
```

Stop Postgres with `make db-down`.

### Grading conformance

`tests/conformance/grading-conformance-v1.json` is a contract shared with the
semantic layer: cases both graders must agree on. It is byte-identical in both
repos with its SHA-256 pinned in both suites, so editing one copy and not the
other fails both. If you change how answers are compared, that file is where the
change has to be argued.

## Repository layout

| Package | Contents |
|---|---|
| `beacon_storage` | SQLAlchemy models, repositories, RLS helpers, Alembic migrations |
| `beacon_iam` | Users, memberships, roles, permissions, OIDC and API-key auth |
| `beacon_runner` | SUT protocol, harness modes, run orchestration, result persistence |
| `beacon_graders` | Grader protocol, shipped graders, comparison tolerance |
| `beacon_ablation` | Leave-one-out attribution and the statistics behind it |
| `beacon_registry` | Suites and item selectors |
| `beacon_benchmarks` | Benchmark adapters, and the importer for curated gold |
| `beacon_workers` | Retention |
| `beacon_ui` | FastAPI routes, CLI, Streamlit dashboard |
| `scripts` | Sweep driver, benchmark ingest, eval-report loader, repo guard |

## Notes

- Integration tests are Postgres-backed; some skip external provider round trips
  unless credentials are configured.
- Multi-tenancy is dormant by default. With only `DATABASE_URL` set, beacon runs
  single-tenant: RLS policies short-circuit until a user context is set, and
  API-key local mode is first-class.
- API keys go in the `X-API-Key` header. `Authorization: Bearer` is for OIDC.

## License

Apache-2.0 — see [LICENSE](LICENSE).
