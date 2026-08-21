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
COPY services/record_pipeline/pyproject.toml services/record_pipeline/pyproject.toml
RUN uv sync --frozen --no-dev --package record-pipeline-service --no-install-project

COPY db/ db/
COPY services/ingestion/src services/ingestion/src
COPY services/mapping/src services/mapping/src
COPY services/normalization/src services/normalization/src
COPY services/validation/src services/validation/src
COPY services/resolution/src services/resolution/src
COPY services/golden/src services/golden/src
COPY services/record_pipeline/src services/record_pipeline/src
COPY services/record_pipeline/tests services/record_pipeline/tests
RUN uv sync --frozen --package record-pipeline-service

# Baked in rather than bind-mounted: a bind mount assumes the container is
# scheduled on a host that has this repository checked out, which is not true
# once pods can land on any node in a cluster. `docker compose run --rm
# pipeline python /tools/ops/reblock.py` and its neighbours keep working
# unmodified -- same path, no longer host-dependent.
COPY tools/ tools/

ENV PCDF_MIGRATIONS_DIR=/app/db/migrations
ENV PYTHONPATH=/app/services/record_pipeline/src
WORKDIR /app/services/record_pipeline

# Drop root. Nothing here needs it: the venv and the app are baked in at build
# time, and the only writable path at runtime is the bind-mounted /data, which
# the host owns. A container that never needs to write outside its own mount has
# no reason to be able to.
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
