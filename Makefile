.PHONY: setup db-up db-down migrate test lint typecheck demo

POSTGRES_PORT ?= 5432
# `beacon` owns the cluster, so it carries rolsuper/rolbypassrls and its
# queries NEVER consult an RLS policy: every policy in this schema is inert
# while the app connects as it, and `require_permission` is the isolation that
# actually holds. 0025 grants a constrained `beacon_app` so that CAN change.
#
# **The serving DSN is deliberately NOT switched yet, and here is what blocks
# it.** Every tenant policy is `FOR ALL USING (...)` with no `WITH CHECK`, and
# Postgres reuses USING as the INSERT check -- so under a constrained role a
# write has to satisfy the same membership predicate a read does. Probed on
# the live cluster as `beacon_app` with the GUC set: `INSERT INTO teams` is
# refused outright, because `TeamService.create` inserts the team before
# granting the creator's membership and the new row is therefore invisible to
# its own author. `add_team_member` inserts a `users` row and a `memberships`
# row for SOMEONE ELSE, so both fail; `remove_team_member` and
# `issue_member_key` read the roster through `memberships_self` and would 404
# a real member; `list_teams`' admin branch would quietly return only the
# actor's teams while its docstring promises all of them.
#
# Closing this needs explicit `WITH CHECK` clauses on those policies and a
# reordering of the team-creation path, neither of which is a config change.
# Until then switching this line trades a real feature for a policy that is
# not yet enforceable.
DATABASE_URL ?= postgresql+psycopg://beacon:beacon_dev@localhost:$(POSTGRES_PORT)/beacon
# The owning DSN, named separately even though it matches today: alembic
# creates tables, policies and indexes, which the serving role must never be
# able to do. Splitting it now is what makes the switch above a one-line
# change once the write paths are fixed.
#
# DEFAULTED FROM $(DATABASE_URL), not written out again. An independent literal
# here is a footgun: `DATABASE_URL=<other-db> make migrate` would then upgrade
# the DEFAULT database and report success against a target the operator never
# named -- and `.env.example` plus the README both train that
# `DATABASE_URL=... <command>` habit. Following it means one variable still
# picks the database; whoever switches the serving role above sets this
# explicitly at the same time, which the note there says to do.
MIGRATE_DATABASE_URL ?= $(DATABASE_URL)
UV ?= uv

setup:
	$(UV) sync --all-packages

db-up:
	docker compose up -d postgres
	@until docker compose exec -T postgres pg_isready -U beacon; do sleep 1; done
# No local password for `beacon_app` is set here. It would be a no-op anyway:
# 0025 CREATES the role, and `migrate` runs after `db-up`, so on a fresh
# cluster there is no role to alter and the step would never run again.
# Nothing local needs it either -- the tests reach the role with SET ROLE,
# which needs no secret. Whoever switches the serving DSN above sets one
# AFTER migrate, and a deployment sets its own out of band.

db-down:
	docker compose down

migrate:
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

demo: db-up migrate
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
