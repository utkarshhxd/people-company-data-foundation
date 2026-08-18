# Operations

Metrics, dashboards, alerts, backup and restore, continuous integration and the test suites.

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

If Postgres is unreachable the endpoint still returns 200 with
`pcdf_metrics_up 0` and no stale gauges — monitoring that dies with the database
is useless exactly when it is needed.

## Alerts

Eleven rules in `../../infra/prometheus/rules/alerts.yml`, delivered to
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


## Operations

The [runbook](../runbook.md) is the operational reference, written for someone
who did not build this: health checks, backup and restore, releasing a stuck
batch, replaying failed rows, working the review queues, and what has to be true
before this is reachable from anywhere but localhost.

`tools/` holds what is run against a live stack rather than shipped in it. It is
grouped by what a script is *for*, not by what it is written in — the whole
directory is mounted read-only at `/tools` in the `pipeline` and `ingestion`
containers, so the path inside a container is `/tools/<group>/<script>`.

**`tools/ops/`** — run against a live stack, by an operator.

| | |
| --- | --- |
| `backup.sh` · `restore_check.sh` | take a dump, and prove it restores |
| `rebuild_golden.py` | recompute golden values after a rule change |
| `reblock.py` | report entities that should have merged, and merge them on request |
| `purge_source.py` | remove a source's data — the only thing here that deletes |
| `load_samples.sh` · `load_testfiles.sh` | drive a directory of files through the pipeline, one at a time |
| `pgadmin-server.json` | pgAdmin server definition, so nobody types connection details |

**`tools/measure/`** — answer a question about the system, change nothing.

| | |
| --- | --- |
| `parity.py` | prove the two processing paths agree |
| `mapping_coverage.py` | what a vendor's layout would need reviewed, before loading it |
| `bench_per_record.py` | throughput of the per-record path |
| `profile_stages.py` | where the time in a record actually goes |
| `concurrency_check.py` | reproduce the concurrent-builder race, and the fix |

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
| `verify_corpus.sql` · `verify_edge_cases.sql` | what the pipeline made of the generated files |
| `compare_runs.sql` | diff two batches of the same source |

## Credentials

Every setting can be supplied three ways, and the most explicit wins:

1. `POSTGRES_PASSWORD=...` — an environment variable;
2. `POSTGRES_PASSWORD_FILE=/path/to/file` — a file to read it from;
3. a file at `/run/secrets/postgres_password` — found without being configured,
   because that is where Compose and Kubernetes both mount one.

The point is what an environment variable is visible to: `docker inspect`, the
environment of anything else in the container, and the shell history of whoever
exported it. A file is visible to whoever can read the file.

`docker-compose.secrets.yml` wires the two credentials that matter through it:

```bash
mkdir -p secrets
printf '%s' 'a-strong-password' > secrets/postgres_password
printf '%s' 'key-one,key-two'   > secrets/pcdf_api_keys

docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d
```

It is an overlay rather than the default because the default has to work on a
fresh clone with nothing but `cp .env.example .env`. It also sets
`PCDF_ALLOW_UNAUTHENTICATED=false`, since with real keys in place leaving the
door open would defeat them.

This is not a secret store. It is the seam that lets one go in front of this
without any service knowing.

## Continuous integration

`.github/workflows/ci.yml` builds all eight images, runs all eight suites
in-container, runs `ruff check`, and applies every
migration against an **empty** database — twice, because a migration that is not
idempotent is a migration that cannot be re-run.

## Running tests

Polars, psycopg's binary driver, and confluent-kafka are all native
extensions, and this host's Application Control policy blocks them outside
Docker — so tests run in a container:

```powershell
docker compose run --rm ingestion python -m pytest tests -v
docker compose run --rm mapping python -m pytest tests -v
docker compose run --rm normalization python -m pytest tests -v
docker compose run --rm validation python -m pytest tests -v
docker compose run --rm resolution python -m pytest tests -v
docker compose run --rm golden python -m pytest tests -v
docker compose run --rm pipeline python -m pytest tests -v

# api has no `uv run` entrypoint in compose, so invoke it explicitly
docker compose run --rm api uv run --frozen --no-sync python -m pytest services/api/tests -v
```
