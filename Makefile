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

test:
	DATABASE_URL=$(DATABASE_URL) $(UV) run pytest

lint:
	$(UV) run ruff check . && $(UV) run ruff format --check .

# scripts/ is in scope deliberately: the ablation runner protocol drifted from
# HarnessRunner for the repo's whole history because its only implementation
# lived here, outside the typecheck target.
typecheck:
	$(UV) run mypy packages/ scripts/

demo: db-up migrate
	DATABASE_URL=$(DATABASE_URL) $(UV) run beacon-worker-promotion &
	DATABASE_URL=$(DATABASE_URL) $(UV) run beacon-worker-convergence &
	DATABASE_URL=$(DATABASE_URL) $(UV) run beacon-worker-antigoodhart &
	DATABASE_URL=$(DATABASE_URL) $(UV) run beacon-worker-retention &
	DATABASE_URL=$(DATABASE_URL) BEACON_DATABASE_URL=$(DATABASE_URL) $(UV) run uvicorn beacon_ui.api.app:app --reload &
	DATABASE_URL=$(DATABASE_URL) $(UV) run beacon demo seed
	DATABASE_URL=$(DATABASE_URL) $(UV) run streamlit run packages/beacon_ui/src/beacon_ui/dashboard/app.py
