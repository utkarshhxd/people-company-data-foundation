FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependency layer caches independently of app code.
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/normalization/pyproject.toml services/normalization/pyproject.toml
RUN uv sync --frozen --no-dev --package normalization-service --no-install-project

COPY db/ db/
COPY services/normalization/src services/normalization/src
COPY services/normalization/tests services/normalization/tests
RUN uv sync --frozen --package normalization-service

ENV PCDF_MIGRATIONS_DIR=/app/db/migrations
ENV PYTHONPATH=/app/services/normalization/src
WORKDIR /app/services/normalization
