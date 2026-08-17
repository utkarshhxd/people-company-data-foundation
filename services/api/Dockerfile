FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Copy workspace manifests first so the dependency layer caches independently of app code.
# --no-install-project defers installing api-service itself, since its src/ isn't copied yet.
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/api/pyproject.toml services/api/pyproject.toml
RUN uv sync --frozen --no-dev --package api-service --no-install-project

COPY services/api/src services/api/src
COPY services/api/tests services/api/tests
# Dev group included so the suite runs in-container like every other service —
# this host's Application Control policy blocks native extensions outside Docker.
RUN uv sync --frozen --package api-service

ENV PYTHONPATH=/app/services/api/src

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

EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-dev", "--no-sync", "uvicorn", "api_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "services/api/src"]
