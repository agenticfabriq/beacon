FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependency layer: manifests only, so editing source does not reinstall the
# world. Every workspace member's pyproject.toml has to be here -- uv resolves
# the whole workspace at once and fails on a member it cannot find, so this
# list is not an optimisation, it is a requirement.
COPY pyproject.toml uv.lock ./
COPY packages/beacon_ablation/pyproject.toml packages/beacon_ablation/
COPY packages/beacon_benchmarks/pyproject.toml packages/beacon_benchmarks/
COPY packages/beacon_graders/pyproject.toml packages/beacon_graders/
COPY packages/beacon_iam/pyproject.toml packages/beacon_iam/
COPY packages/beacon_registry/pyproject.toml packages/beacon_registry/
COPY packages/beacon_runner/pyproject.toml packages/beacon_runner/
COPY packages/beacon_storage/pyproject.toml packages/beacon_storage/
COPY packages/beacon_ui/pyproject.toml packages/beacon_ui/
COPY packages/beacon_workers/pyproject.toml packages/beacon_workers/

# --frozen fails on a stale lockfile rather than quietly resolving something
# the test suite never ran against. --no-install-workspace is what keeps this
# layer cacheable: without it the workspace source is needed here too.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-packages --frozen --no-install-workspace

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --all-packages --frozen

FROM python:3.13-slim AS runtime
RUN useradd --create-home --uid 10001 beacon
WORKDIR /app
COPY --from=build --chown=beacon:beacon /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER beacon
EXPOSE 8000
CMD ["uvicorn", "beacon_ui.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
