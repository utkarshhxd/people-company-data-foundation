FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependency layer caches independently of app code.
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/validation/pyproject.toml services/validation/pyproject.toml
RUN uv sync --frozen --no-dev --package validation-service --no-install-project

COPY db/ db/
COPY services/validation/src services/validation/src
COPY services/validation/tests services/validation/tests
RUN uv sync --frozen --package validation-service

ENV PCDF_MIGRATIONS_DIR=/app/db/migrations
ENV PYTHONPATH=/app/services/validation/src
WORKDIR /app/services/validation
