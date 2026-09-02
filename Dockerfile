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
# No trust list here, deliberately. (--proxy-headers is uvicorn's default and
# is written out only to make the dependency visible; it carries no behaviour.)
#
# uvicorn honours X-Forwarded-Proto only from addresses in
# --forwarded-allow-ips, which defaults to $FORWARDED_ALLOW_IPS or 127.0.0.1.
# Behind a reverse proxy on a container network that default drops the header,
# and `request.url_for("oidc_callback")` in routes/auth.py would hand the IdP
# an http:// redirect_uri for an https-only site. Inert while oidc_issuer is
# empty; scheduled to break on the day someone enables single sign-on.
#
# So the deployment must set FORWARDED_ALLOW_IPS to the proxy's address, next
# to the network that defines it -- beacon-internal's deploy/compose.yaml does,
# pinning Caddy to a fixed address and naming exactly that. If it is ever unset,
# the failure is silent in the direction described above. NOT `*`:
# `*` puts uvicorn on its always-trust path, where the client address becomes
# the LEFTMOST X-Forwarded-For entry -- appended to by the proxy, so
# attacker-chosen even in a correct deployment -- instead of walking the chain
# back past trusted hops. A CIDR gets the scheme fixed and keeps the walk.
#
# Unset, this image trusts loopback only. That is the right default for an
# image that can also be run directly.
CMD ["uvicorn", "beacon_ui.api.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers"]
