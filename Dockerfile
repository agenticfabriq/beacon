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
# image that serves HTTP. It does NOT shrink the big wheels: measured in the
# built image, site-packages is 767 MB and the top five are pyarrow 140,
# scipy 118, pandas 74, plotly 67 and duckdb 53 -- every one a declared
# runtime dependency of beacon_ablation, beacon_ui or beacon_benchmarks.
# Anyone shrinking this image starts at pyarrow, not at the dev group.
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
# --forwarded-allow-ips=* is required, and it is only safe because of how this
# image is deployed. uvicorn honours X-Forwarded-Proto only from addresses in
# that list, which defaults to 127.0.0.1; Caddy reaches this container over the
# compose network, not loopback, so without it every request looks like plain
# http to the app. `request.url_for("oidc_callback")` in routes/auth.py builds
# the redirect_uri from that scheme, so the IdP would be handed an http:// URI
# on an https-only site. Inert while oidc_issuer is empty -- it breaks on the
# day OIDC is switched on, which is the worst day to discover it.
#
# `*` trusts the header from anyone who can reach port 8000, so this container
# MUST stay unpublished: compose gives it `expose:`, never `ports:`, and Caddy
# is the only thing on that network. Publishing it directly would let any
# client claim https.
CMD ["uvicorn", "beacon_ui.api.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
