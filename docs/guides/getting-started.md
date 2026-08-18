# Getting started

Prerequisites, first boot, and how to tell the stack is actually up.

Part of the [People & Company Data Foundation](../../README.md).

## Prerequisites

- Docker Desktop (or engine) with Compose v2
- [uv](https://docs.astral.sh/uv/) for local (non-Docker) Python development

## Quickstart

```powershell
Copy-Item .env.example .env
# edit .env if you want non-default credentials/ports

docker compose build
docker compose up -d
docker compose ps    # wait until every service shows (healthy)
```

> Note: the dockerized Postgres is mapped to host port **5433** by default
> (`POSTGRES_PORT` in `.env`), not 5432 — this avoids clashing with a
> Postgres instance that may already be running natively on this machine.
> **Connect pgAdmin to `localhost:5433`**, not 5432; there is no native install
> to find, and the data lives in the `pcdf_postgres-data` Docker volume.
>
> Grafana is on **3001** for the same reason: 3000 is the port every Node dev
> server wants, and when it is already taken Docker Desktop can leave the
> container up and healthy but unreachable from the host rather than failing
> loudly. If a page on one of these ports looks like somebody else's app, it is.

## Verifying the stack

```powershell
# Liveness (no downstream checks)
curl.exe -i http://localhost:8000/health/live

# Readiness (actively checks Postgres + Kafka connectivity)
curl.exe -i http://localhost:8000/health/ready

# Prometheus metrics exposition
curl.exe -i http://localhost:8000/metrics

# Prometheus itself
curl.exe -i http://localhost:9090/-/healthy
curl.exe "http://localhost:9090/api/v1/query?query=up%7Bjob%3D%22api-service%22%7D"

# Grafana (provisioned Prometheus datasource + starter dashboard)
curl.exe -i http://localhost:3001/api/health
```

Open http://localhost:3001 (credentials from `.env`) and check
Connections > Data sources > Prometheus > "Save & test", and the
"Service Health" dashboard under Dashboards.
