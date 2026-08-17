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
