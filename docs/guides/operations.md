# Operations

Metrics, alerts, backup and restore, continuous integration, the test suites,
and what lives under `tools/`.

Part of the [People & Company Data Foundation](../../README.md).

## Metrics and dashboards

The **Data Foundation** dashboard in Grafana (http://localhost:3001) watches the
failure modes that are otherwise silent — the ones where every container stays
green while the data quietly degrades:

- a quarantined record loses nothing by waiting, and nothing ages the queue
- an unreviewed mapping means values are captured but attributed to no field,
  so they are never validated or resolved on
- an open match candidate means one real entity is represented by two ids

```powershell
curl.exe -s http://localhost:8000/metrics | Select-String "^pcdf_"
```

```
pcdf_quarantine_items{status="open"} 3.0
pcdf_oldest_open_quarantine_age_seconds 60523.33
pcdf_column_mappings{status="needs_review"} 2.0
pcdf_records_validated{status="invalid"} 5.0
pcdf_entities{entity_type="company",status="active"} 18.0
pcdf_golden_contested_values 9.0
pcdf_golden_mean_confidence{entity_type="company"} 0.667
```

These are **gauges queried from Postgres at scrape time**, not counters
incremented in each service. Queue depth is an accumulated-state question, and
an in-process counter answers it badly: it resets on restart and double-counts
after a Kafka replay. Results are cached 12s against a 15s scrape interval.

**The age metrics are the ones to alert on.** A count of 3 is fine; a count of 3
that has been 3 for a fortnight is a process failure, and no count alone reveals
that.

Two things are read from somewhere other than Postgres, and they fail
independently of it and of each other. `pcdf_kafka_consumer_lag` comes from the
broker, so a database outage does not blank it out and a broker outage does not
blank out the queue depths. `pcdf_service_heartbeat_age_seconds` comes from the
`service_heartbeat` table, which every consumer and the watcher upsert into as
they turn their loop -- the container healthcheck reads a file, but a file is
only visible inside the container that wrote it, and "is the watcher sweeping"
is a question asked from outside.

If Postgres is unreachable the endpoint still returns 200 with
`pcdf_metrics_up 0` and no stale gauges — monitoring that dies with the database
is useless exactly when it is needed. `pcdf_kafka_up` does the same for the
broker. Serving the last scrape's numbers instead would be worse than serving
none: a queue depth from ten minutes ago, presented as current, is what somebody
makes a decision on.

## Alerts

Sixteen rules in `../../infra/prometheus/rules/alerts.yml`, delivered to
Alertmanager at http://localhost:9093. Almost all of them alert on **age rather
than depth**: a queue of three is fine, three that have not moved in a fortnight
means reviewing stopped, and no count reveals that.

What is firing is visible at **http://localhost:8000/alerts/page**, backed by
`GET /alerts`. Both are read-only projections: rules, grouping, inhibition and
resolution stay in Prometheus and Alertmanager, and no notification channel is
encoded in the API — so adding Slack is a routing change that touches no
application code.

### Routing them somewhere real

With `ALERTMANAGER_WEBHOOK_URL` unset, the route uses a receiver with no
destination: alerts collect in the UI at :9093 and nothing leaves the machine.
Set it and every alert is delivered:

```bash
# .env — Slack, Teams and Discord all accept a URL of this shape
ALERTMANAGER_WEBHOOK_URL=https://hooks.slack.com/services/...

docker compose up -d alertmanager
```

The config is a template rendered at container start, because Alertmanager does
not expand environment variables in its own config. The URL is a credential —
anyone holding it can post into that channel — so it is written to a file with
`umask 077` and read via `url_file`. It is never substituted into the config, so
it is not in the image and not in `docker inspect`. Unsetting the variable
removes the file rather than leaving a live credential in the volume.

Prove delivery without waiting for a real alert:

```bash
docker compose exec -T alertmanager amtool alert add alertname=DeliveryTest \
    severity=critical --alertmanager.url=http://localhost:9093
```

`severity=critical` has `group_wait: 0s`, so it is sent immediately.

See `../runbook.md` for the full table of rules and what to do about each.

## `tools/`

Run against a live stack rather than shipped in it. Grouped by what a script
is *for*, not by what it's written in — the whole directory is mounted
read-only at `/tools` in the `pipeline` and `ingestion` containers, so the
path inside a container is `/tools/<group>/<script>`.

**`tools/ops/`** — run against a live stack, by an operator.

| | |
| --- | --- |
| `backup.sh` · `restore_check.sh` | take a dump, and prove it restores |
| `rebuild_golden.py` | recompute golden values after a rule change |
| `reblock.py` | report entities that should have merged, and merge them on request |
| `purge_source.py` | remove a source's data — the only thing here that deletes |
| `project_serving.py` | copy the golden record onto the application database that displays it |
| `load_samples.sh` · `load_testfiles.sh` | drive a directory of files through the pipeline, one at a time |
| `pgadmin-server.json` | pgAdmin server definition, so nobody types connection details |

**`tools/fixtures/`** — produce data to run the system against.

| | |
| --- | --- |
| `sample_file.py` | cut a bounded sample off a large export, streaming |
| `generate_fixture.py` | a synthetic file at a chosen size |
| `generate_test_corpus.py` | a multi-vendor corpus with known overlaps |
| `generate_edge_cases.py` | one file of everything that has ever broken a stage |
| `golden_sample.py` | pull a readable sample of golden records back out |

**`tools/sql/`** — hand verification, for psql or pgAdmin.

| | |
| --- | --- |
| `verify.sql` | twelve reconciliation checks; empty output is the pass |
| `queries.sql` | ten worked queries |
| `compare_runs.sql` | diff two batches of the same source |

**`db/serving/`** — not part of `tools/`, but in the same category: run by hand,
against a database this repository does not own. See
[Serving an application](serving-projection.md).

## Credentials

Every setting can be supplied three ways, most explicit wins:

1. `POSTGRES_PASSWORD=...` — an environment variable
2. `POSTGRES_PASSWORD_FILE=/path/to/file` — a file to read it from
3. a file at `/run/secrets/postgres_password` — found without being
   configured, because that's where Compose and Kubernetes both mount one

The point is what an environment variable is visible to: `docker inspect`,
the environment of anything else in the container, and the shell history of
whoever exported it. A file is visible to whoever can read the file.

```bash
mkdir -p secrets
printf '%s' 'a-strong-password' > secrets/postgres_password

docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d
```

It's an overlay rather than the default because the default has to work on a
fresh clone with nothing but `cp .env.example .env`. This is not a secret
store — it's the seam that lets one go in front of this without any service
knowing.

## Continuous integration

`.github/workflows/ci.yml` runs three jobs: `ruff check` lint, every
migration applied twice against an empty database (a migration that isn't
idempotent can't be re-run), and all **seven** service images
(`ingestion`, `mapping`, `normalization`, `validation`, `resolution`,
`golden`, `record_pipeline`) built and their test suites run in-container,
plus `libs/common`'s suite riding along on the `ingestion` build.

CI needs nothing beyond Postgres: every suite tests functions that talk to the
database, and the consumers are a thin shell over those same functions, so
none of them needs a broker to be exercised.

## Running tests

Polars and psycopg's binary driver are both native extensions; this
environment's Application Control policy blocks them outside Docker, so
tests run in a container — matching CI exactly, so "works in CI" and "works
here" never drift apart:

```powershell
docker compose run --rm ingestion python -m pytest tests -v
docker compose run --rm mapping python -m pytest tests -v
docker compose run --rm normalization python -m pytest tests -v
docker compose run --rm validation python -m pytest tests -v
docker compose run --rm resolution python -m pytest tests -v
docker compose run --rm golden python -m pytest tests -v
docker compose run --rm pipeline python -m pytest tests -v

# common has no image of its own; any of the above carries it
docker compose run --rm ingestion python -m pytest ../../libs/common/tests -v
```

## Resetting for a clean test

Wipes all data, keeps the schema fresh via migrations:

```powershell
docker compose down -v          # stops everything, deletes the postgres volume
docker compose up -d postgres migrate watcher
```

Watch folders (`data/inbox/watch/<feed>/`) aren't touched by this — clear
`_done/`/`_failed/` by hand if you want a folder to look untouched again;
`feed.json` is config, not data, and is safe to leave alone.
