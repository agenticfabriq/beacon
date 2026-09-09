.PHONY: setup db-up db-down migrate app-role-password test lint typecheck demo

POSTGRES_PORT ?= 5432
# TWO roles, and the split is the point. `beacon` owns the cluster, so it
# carries rolsuper/rolbypassrls and its queries never consult an RLS policy --
# every policy in this schema was inert while the app connected as it, and
# `require_permission` was the only isolation.
#
# 0025 grants a constrained `beacon_app`; 0026 rewrites the tenant policies so
# team administration works under one. Both halves were needed: with 0025
# alone, RLS refused the WRITES -- policies were `FOR ALL USING (...)` with no
# `WITH CHECK`, so Postgres reused USING as the insert check and a team could
# not be created by someone not yet a member of it.
#
# The serving DSN. Anything that answers a request uses this.
DATABASE_URL ?= postgresql+psycopg://beacon_app:beacon_dev@localhost:$(POSTGRES_PORT)/beacon
# The OWNING DSN, for DDL only: alembic creates tables, policies and indexes,
# which the serving role must never be able to do.
#
# Derived from $(DATABASE_URL) by substituting the role, so one variable still
# picks the DATABASE -- an independent literal here was a footgun, because
# `DATABASE_URL=<other-db> make migrate` then upgraded the DEFAULT database
# and reported success against a target the operator never named, and both
# `.env.example` and the README train that habit.
#
# The substitution only works when the serving role is `beacon_app` AND the
# two roles share a password, which is true locally and generally false in a
# deployment. So `migrate` refuses to run unless the result names the owning
# role: silently running DDL as the serving role would fail on the first
# CREATE TABLE, and doing it against the wrong database would not fail at all.
# A deployment sets MIGRATE_DATABASE_URL explicitly.
#
# The check accepts `//beacon:` and `//beacon@` -- with an inline password and
# without. The first version required the colon, which rejected
# `postgresql+psycopg://beacon@host/db`: the ordinary shape for .pgpass,
# PGPASSWORD or peer auth, and therefore the exact deployment this guard was
# written for, refused with a message claiming the DSN did not name `beacon`.
MIGRATE_DATABASE_URL ?= $(subst //beacon_app:,//beacon:,$(DATABASE_URL))
UV ?= uv

setup:
	$(UV) sync --all-packages

db-up:
	docker compose up -d postgres
	@until docker compose exec -T postgres pg_isready -U beacon; do sleep 1; done
	@# `beacon_test` as well as `beacon`. compose creates only POSTGRES_DB, and
	@# `make test` points at beacon_test -- so a fresh clone used to reach the
	@# first `make test` and get `database "beacon_test" does not exist`, with
	@# nothing in the repo that would have created it. CI never saw this: its
	@# service container is handed POSTGRES_DB: beacon_test directly.
	@docker compose exec -T postgres psql -U beacon -d beacon -tAc \
	  "SELECT 1 FROM pg_database WHERE datname='beacon_test'" | grep -q 1 \
	  || docker compose exec -T postgres createdb -U beacon beacon_test

db-down:
	docker compose down

migrate:
	@case "$(MIGRATE_DATABASE_URL)" in \
	  *//beacon:*|*//beacon@*) ;; \
	  *) echo "MIGRATE_DATABASE_URL does not name the owning role 'beacon'." >&2; \
	     echo "  got: $(MIGRATE_DATABASE_URL)" >&2; \
	     echo "Set it explicitly -- the default derives it from DATABASE_URL by" >&2; \
	     echo "substituting beacon_app for beacon, which only works when the" >&2; \
	     echo "serving role is beacon_app and the two share a password." >&2; \
	     exit 1 ;; \
	esac
	DATABASE_URL=$(MIGRATE_DATABASE_URL) $(UV) run alembic -c packages/beacon_storage/src/beacon_storage/migrations/alembic.ini upgrade head

# Deliberately NOT $(DATABASE_URL): the fixtures drop and recreate the public
# schema, so pointing this at the dev database destroys its run history.
# The OWNING role, because the fixtures drop and recreate the public schema
# and then drop to `beacon_app` themselves to exercise the policies. A test run
# that could not do DDL would have nothing to test against.
TEST_DATABASE_URL ?= postgresql+psycopg://beacon:beacon_dev@localhost:$(POSTGRES_PORT)/beacon_test

test:
	DATABASE_URL=$(TEST_DATABASE_URL) $(UV) run pytest

lint:
	$(UV) run ruff check . && $(UV) run ruff format --check .

# scripts/ is in scope deliberately: the ablation runner protocol drifted from
# HarnessRunner for the repo's whole history because its only implementation
# lived here, outside the typecheck target.
typecheck:
	$(UV) run mypy packages/ scripts/

# The serving role's LOCAL password, a throwaway beside the one already in
# docker-compose.yml. It lives here rather than in a migration because a
# checked-in migration must not carry a secret -- 0025 creates the role and
# grants it, and leaves it unable to authenticate until someone does this.
#
# AFTER migrate, not in `db-up`: 0025 is what CREATES the role, so on a fresh
# cluster there is nothing to alter yet. An earlier version put it in `db-up`
# and it was a silent no-op for exactly that reason -- the role got LOGIN with
# no secret and `make demo` then failed connecting as it.
#
# A deployment sets its own out of band and never through this file.
app-role-password: migrate
	@docker compose exec -T postgres psql -U beacon -d beacon -c \
	  "ALTER ROLE beacon_app PASSWORD 'beacon_dev'" >/dev/null
	@echo "beacon_app password set for local development"

demo: db-up migrate app-role-password
	DATABASE_URL=$(DATABASE_URL) $(UV) run beacon-worker-retention &
	DATABASE_URL=$(DATABASE_URL) $(UV) run beacon demo seed
	@echo ""
	@echo "UI:   http://localhost:8000/ui"
	@echo "Sign in with an API key. The seed prints one on a fresh database; a"
	@echo "re-run says 'existing key not reprinted' and the first run's key still works."
	@echo "Ctrl-C stops the API and the retention worker with it."
	@echo ""
# Last, and NOT backgrounded: the API has to hold the foreground so Ctrl-C
# ends the demo. When `streamlit run` was here it did that by accident;
# replacing it with an echo returned straight to the prompt and orphaned
# every backgrounded child, with no PID printed and no demo-down target.
# Its own "Uvicorn running on ..." line is also the readiness signal, which
# an echo printed before the port binds is not.
	DATABASE_URL=$(DATABASE_URL) BEACON_DATABASE_URL=$(DATABASE_URL) $(UV) run uvicorn beacon_ui.api.app:app --reload
