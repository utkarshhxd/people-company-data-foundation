# ADR 0001: Increment 1 infrastructure choices

## Status

Accepted.

## Context

Increment 1 scaffolds the local infrastructure for the People & Company Data
Foundation before any pipeline logic exists: repo structure, Docker Compose,
PostgreSQL, Kafka, Prometheus/Grafana, a basic Python service, and health
checks. The project's hard constraints: Python only, PostgreSQL, Apache
Kafka, Docker/Docker Compose for local dev, Grafana + Prometheus for
monitoring, UUIDs for entity IDs, migrations for schema, no hardcoded
credentials.

## Decisions

- **uv workspace** (root `pyproject.toml`, `services/*` + `libs/*` members,
  `package = false`) instead of per-service dependency management or
  poetry. The 17-step roadmap implies multiple future pipeline services and
  a shared library for reusable code (field-type normalizers, canonical
  schema models). A workspace gives one lockfile/venv and path-dependencies
  between services/libs with no restructuring later. Trade-off: Dockerfiles
  need repo-root build context to reach `libs/`.

- **Kafka: KRaft mode, official `apache/kafka` image**, not ZooKeeper, not
  Bitnami/Confluent. ZooKeeper mode was removed in Kafka 4.0 — KRaft is the
  only forward path, and single-node combined broker+controller is the
  documented local-dev shape. `apache/kafka` is upstream OSS with no vendor
  extensions, matching the "Apache Kafka" constraint (Bitnami's 2025 shift
  to a paid image catalog makes it a poor long-term bet; Confluent images
  carry platform assumptions this project doesn't need).

- **Basic Python service: FastAPI + uvicorn**, not Flask or stdlib
  `http.server`. Async-native (the readiness check probes Postgres and
  Kafka concurrently), trivial Prometheus instrumentation via
  `prometheus-fastapi-instrumentator`, and this service will accrete many
  more endpoints across later increments.

- **Postgres driver: `psycopg` v3** (async-native, built-in pooling).
  **Kafka client: `confluent-kafka`** (librdkafka-backed, the de facto
  production-grade Python Kafka client). `kafka-python` had a multi-year
  maintenance gap; `aiokafka` is reasonable but less standard for the real
  producers/consumers later increments will need.

- **Liveness vs readiness split**: `/health/live` is dependency-free (used
  by Docker's own healthcheck, so a transient Postgres/Kafka blip never
  kills the app container). `/health/ready` actively checks Postgres
  (`SELECT 1`) and Kafka (`AdminClient.list_topics()`) concurrently and
  returns 503 naming the failing check — this is the real "infra works"
  signal.

- **`db/migrations/` created but empty** — just a home for later; no
  migration tool decision (Alembic vs raw SQL) or schema design happens in
  this increment.

- **No hardcoded credentials**: `.env.example` committed with placeholders,
  `.env` gitignored; each compose service's `environment:` block lists only
  the variables it needs.

- **Postgres host port 5433, not 5432**: a native (non-Docker) Postgres was
  found already listening on 5432 on the development machine this was
  built on; 5433 avoids the conflict by default and is configurable via
  `.env`.

- **Kafka `CLUSTER_ID` left unset**: confirmed via the upstream
  `apache/kafka` Docker docs that a cluster ID is not mandatory — the image
  generates one automatically on first start if omitted, removing a manual
  setup step with no loss of correctness for a single local dev cluster.

- **Image tags** (verified against the registries at the time this
  increment was built): `postgres:18.4-alpine`, `apache/kafka:4.3.1`,
  `prom/prometheus:v3.13.2`, `grafana/grafana:13.1.1`. All pinned to exact
  tags, never `:latest`.

## Operational fixes found during end-to-end verification

Three real issues only surfaced by actually running `docker compose up` and
exercising the stack (not visible from reading the compose file alone):

- **Postgres 18+ changed its expected volume mount point.** Mounting the
  named volume at `/var/lib/postgresql/data` (the pre-18 convention) makes
  the image refuse to start ("data in an unused mount/volume"). Fixed by
  mounting at `/var/lib/postgresql` instead, per the image's own guidance.

- **The Kafka container's non-root user (`appuser`, uid 1000) couldn't
  write to the `kafka-data` named volume.** Docker creates named volumes
  root-owned by default, and the target path (`/tmp/kraft-combined-logs`)
  doesn't pre-exist in the image with different permissions. Fixed with a
  `kafka-data-init` one-shot `busybox` service that `chown`s the volume to
  `1000:1000` before Kafka starts (`depends_on: condition:
  service_completed_successfully`).

- **The `PLAINTEXT` listener was bound to the `kafka` hostname's resolved
  IP instead of `0.0.0.0`**, so `localhost:9092` inside the container
  (used by both the Docker healthcheck and the API service's Kafka admin
  client) wasn't reachable. Fixed by binding `KAFKA_LISTENERS` to
  `0.0.0.0` for the `PLAINTEXT`/`CONTROLLER` listeners while keeping
  `KAFKA_ADVERTISED_LISTENERS` as `kafka:9092` for other containers on the
  network to resolve by DNS name.

All three are the kind of issue that only shows up when you actually stand
the stack up — a reminder that "the compose file looks right" is not the
same as "the compose file works," and why the verification plan below
includes running every check against the live stack, not just a config
review.
