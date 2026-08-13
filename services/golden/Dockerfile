FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependency layer caches independently of app code.
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/golden/pyproject.toml services/golden/pyproject.toml
RUN uv sync --frozen --no-dev --package golden-service --no-install-project

COPY db/ db/
COPY services/golden/src services/golden/src
COPY services/golden/tests services/golden/tests
RUN uv sync --frozen --package golden-service

ENV PCDF_MIGRATIONS_DIR=/app/db/migrations
ENV PYTHONPATH=/app/services/golden/src
WORKDIR /app/services/golden
