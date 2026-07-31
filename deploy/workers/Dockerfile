# Single image, four entry-points. Pick the worker command in compose/k8s.

FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY packages/ ./packages/

RUN uv sync --no-dev --no-editable --package beacon_workers

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r beacon \
    && useradd -r -g beacon -d /app -s /sbin/nologin beacon

WORKDIR /app
COPY --from=builder --chown=beacon:beacon /app/.venv /app/.venv
COPY --chown=beacon:beacon packages/ /app/packages/
COPY --chown=beacon:beacon pyproject.toml /app/

USER beacon

CMD ["beacon-worker-promotion"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://127.0.0.1:9101/healthz || exit 1
