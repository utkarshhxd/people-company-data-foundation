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

EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-dev", "--no-sync", "uvicorn", "api_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "services/api/src"]
