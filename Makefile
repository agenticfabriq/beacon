.PHONY: setup db-up db-down migrate test lint typecheck demo

POSTGRES_PORT ?= 5432
DATABASE_URL ?= postgresql+psycopg://beacon:beacon_dev@localhost:$(POSTGRES_PORT)/beacon
UV ?= uv

setup:
	$(UV) sync --all-packages

db-up:
	docker compose up -d postgres
	@until docker compose exec -T postgres pg_isready -U beacon; do sleep 1; done

db-down:
	docker compose down

migrate:
	DATABASE_URL=$(DATABASE_URL) $(UV) run alembic -c packages/beacon_storage/src/beacon_storage/migrations/alembic.ini upgrade head

# Deliberately NOT $(DATABASE_URL): the fixtures drop and recreate the public
# schema, so pointing this at the dev database destroys its run history.
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
