FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependency layer caches independently of app code.
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/resolution/pyproject.toml services/resolution/pyproject.toml
RUN uv sync --frozen --no-dev --package resolution-service --no-install-project

COPY db/ db/
COPY services/resolution/src services/resolution/src
COPY services/resolution/tests services/resolution/tests
RUN uv sync --frozen --package resolution-service

ENV PCDF_MIGRATIONS_DIR=/app/db/migrations
ENV PYTHONPATH=/app/services/resolution/src
WORKDIR /app/services/resolution
