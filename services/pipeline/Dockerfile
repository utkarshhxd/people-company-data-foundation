FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependency layer caches independently of app code. This service composes the
# other five, so their manifests are part of its dependency layer.
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/ingestion/pyproject.toml services/ingestion/pyproject.toml
COPY services/mapping/pyproject.toml services/mapping/pyproject.toml
COPY services/normalization/pyproject.toml services/normalization/pyproject.toml
COPY services/validation/pyproject.toml services/validation/pyproject.toml
COPY services/resolution/pyproject.toml services/resolution/pyproject.toml
COPY services/golden/pyproject.toml services/golden/pyproject.toml
COPY services/pipeline/pyproject.toml services/pipeline/pyproject.toml
RUN uv sync --frozen --no-dev --package record-pipeline-service --no-install-project

COPY db/ db/
COPY services/ingestion/src services/ingestion/src
COPY services/mapping/src services/mapping/src
COPY services/normalization/src services/normalization/src
COPY services/validation/src services/validation/src
COPY services/resolution/src services/resolution/src
COPY services/golden/src services/golden/src
COPY services/pipeline/src services/pipeline/src
COPY services/pipeline/tests services/pipeline/tests
RUN uv sync --frozen --package record-pipeline-service

ENV PCDF_MIGRATIONS_DIR=/app/db/migrations
ENV PYTHONPATH=/app/services/pipeline/src
WORKDIR /app/services/pipeline
