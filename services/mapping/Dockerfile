FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependency layer caches independently of app code.
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/mapping/pyproject.toml services/mapping/pyproject.toml
RUN uv sync --frozen --no-dev --package mapping-service --no-install-project

COPY db/ db/
COPY services/mapping/src services/mapping/src
COPY services/mapping/tests services/mapping/tests
RUN uv sync --frozen --package mapping-service

ENV PCDF_MIGRATIONS_DIR=/app/db/migrations
ENV PYTHONPATH=/app/services/mapping/src
WORKDIR /app/services/mapping
