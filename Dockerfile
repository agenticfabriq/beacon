# syntax=docker/dockerfile:1.7-labs
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /app

# UV_PYTHON_DOWNLOADS=0 is load-bearing, not a preference. uv defaults to
# python-preference=managed and will fetch its own CPython into
# /root/.local/share/uv/python -- outside /app, and so outside the only thing
# the runtime stage copies. The venv's interpreter symlink would dangle and
# CMD would fail to exec. Pinned to the base image's interpreter instead.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

# Dependency layer: manifests only, so editing source does not reinstall the
# world. --parents keeps each member's path, which a plain glob would flatten.
# Written as a glob rather than nine COPY lines because uv resolves the whole
# workspace at once and fails on a member it cannot find: hand-listing them
# duplicates `members = ["packages/*"]` from pyproject.toml with nothing
# enforcing the copy, and a tenth package would break this build with no
# signal until someone built it by hand.
COPY pyproject.toml uv.lock ./
COPY --parents packages/*/pyproject.toml ./

# --frozen fails on a stale lockfile rather than quietly resolving something
# the test suite never ran against. --no-install-workspace is what keeps this
# layer cacheable: without it the workspace source is needed here too.
# --no-dev keeps mypy, pytest, ruff, hypothesis and statsmodels out of an
# image that serves HTTP. Not scipy or pandas: those are declared runtime
# dependencies of beacon_ablation, beacon_ui and beacon_benchmarks, so they
# ship either way and most of the 1.26 GB is them.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-packages --frozen --no-install-workspace --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --all-packages --frozen --no-dev

FROM python:3.13-slim AS runtime
RUN useradd --create-home --uid 10001 beacon
WORKDIR /app
COPY --from=build --chown=beacon:beacon /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER beacon
EXPOSE 8000
CMD ["uvicorn", "beacon_ui.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
