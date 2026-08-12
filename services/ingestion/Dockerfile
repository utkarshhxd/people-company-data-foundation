FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependency layer caches independently of app code.
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/ingestion/pyproject.toml services/ingestion/pyproject.toml
RUN uv sync --frozen --no-dev --package ingestion-service --no-install-project

COPY db/ db/
COPY services/ingestion/src services/ingestion/src
COPY services/ingestion/tests services/ingestion/tests
RUN uv sync --frozen --package ingestion-service

ENV PCDF_MIGRATIONS_DIR=/app/db/migrations
ENV PYTHONPATH=/app/services/ingestion/src
WORKDIR /app/services/ingestion
