FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Copy workspace manifests first so the dependency layer caches independently of
# app code. --no-install-project defers installing review-console-service
# itself, since its src/ isn't copied yet.
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
# The console delegates every decision to the stage that owns it, so those
# packages -- normalization, which enrichment's accept path reuses to
# normalize a confirmed value, and ingestion, which the upload form's
# background job calls the same way the `ingest` CLI does -- are part of
# this image's dependency layer.
COPY services/ingestion/pyproject.toml services/ingestion/pyproject.toml
COPY services/mapping/pyproject.toml services/mapping/pyproject.toml
COPY services/validation/pyproject.toml services/validation/pyproject.toml
COPY services/resolution/pyproject.toml services/resolution/pyproject.toml
COPY services/golden/pyproject.toml services/golden/pyproject.toml
COPY services/normalization/pyproject.toml services/normalization/pyproject.toml
COPY services/enrichment/pyproject.toml services/enrichment/pyproject.toml
COPY services/review_console/pyproject.toml services/review_console/pyproject.toml
RUN uv sync --frozen --no-dev --package review-console-service --no-install-project

COPY services/ingestion/src services/ingestion/src
COPY services/mapping/src services/mapping/src
COPY services/validation/src services/validation/src
COPY services/resolution/src services/resolution/src
COPY services/golden/src services/golden/src
COPY services/normalization/src services/normalization/src
COPY services/enrichment/src services/enrichment/src
COPY services/review_console/src services/review_console/src
COPY services/review_console/tests services/review_console/tests
# Dev group included so the suite runs in-container like every other service --
# this host's Application Control policy blocks native extensions outside Docker.
RUN uv sync --frozen --package review-console-service

ENV PYTHONPATH=/app/services/review_console/src

# The Operations tab runs the reconciliation checks in tools/sql/verify.sql,
# and reads them from an absolute /tools/sql/verify.sql
# (review_console/src/review_console/operations.py). Compose supplies that as a
# read-only bind mount of ./tools, which assumes the container is scheduled on
# a host with this repository checked out -- not true once a pod can land on
# any node. Baked in at the same path, so the bind mount still overlays
# identical content and nothing about the Compose path changes.
COPY tools/ /tools
WORKDIR /app/services/review_console

# Drop root. Nothing here needs it: the venv and the app are baked in at build
# time, and the one bind mount this service has (./data, for uploaded files)
# is written under a path this user owns.
#
# uv wants a cache directory and would put it under $HOME, which a service
# account has no reason to have. Pointing it inside /app -- already owned by the
# user -- avoids provisioning a home just to hold a cache that --frozen
# --no-sync barely uses.
#
# Added last so every COPY and every uv sync above still runs as root and the
# layer cache is unaffected.
ENV UV_CACHE_DIR=/app/.uv-cache
RUN groupadd --system --gid 10001 app  && useradd --system --uid 10001 --gid app --no-create-home app  && mkdir -p /app/.uv-cache  && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-dev", "--no-sync", "uvicorn", "review_console.main:app", "--host", "0.0.0.0", "--port", "8000"]
