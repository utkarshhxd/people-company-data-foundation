# People & Company Data Foundation

A local-first data foundation that ingests messy People/Company records,
normalizes and validates them, resolves them to real-world entities, and
stores trusted canonical data with full history and provenance.

This repository is being built incrementally.

- **Increment 1** — local infrastructure: Docker Compose, PostgreSQL, Kafka,
  Prometheus/Grafana, a basic Python service, health checks.
  (`docs/decisions/0001-increment-1-infra.md`)
- **Increment 2** — ingestion: read messy CSV/Excel and record every row
  verbatim as an observation with provenance.
  (`docs/decisions/0002-increment-2-ingestion.md`)
- **Increment 3** — schema mapping: decide which canonical field each source
  column represents, with method, confidence, evidence, and a human review
  path. (`docs/decisions/0003-increment-3-schema-mapping.md`)
- **Increment 4** — normalization + lossless attribute capture: every column of
  every row becomes an observation, raw and normalized side by side.
  (`docs/decisions/0004-increment-4-normalization.md`)
- **Increment 5** (this state) — validation: judge whether each observed value
  is usable as the field it was mapped to, and whether the record could ever be
  resolved to an entity. (`docs/decisions/0005-increment-5-validation.md`)

## Repository layout

```
docker-compose.yml       # postgres, kafka, prometheus, grafana, api, migrate, ingestion
pyproject.toml           # uv workspace root
infra/                   # prometheus + grafana provisioning config
db/migrations/           # numbered .sql migrations, applied by the `migrate` service
libs/common/             # shared: settings, db, migration runner, Kafka event shapes
services/api/            # FastAPI service with health checks
services/ingestion/      # CSV/Excel ingestion CLI
services/mapping/        # schema-mapping consumer + map-schema/review CLIs
services/normalization/  # normalization consumer + normalize CLI
services/validation/     # validation consumer + validate CLI
data/inbox/              # local drop dir, bind-mounted into the ingestion container
```

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
curl.exe -i http://localhost:3000/api/health
```

Open http://localhost:3000 (credentials from `.env`) and check
Connections > Data sources > Prometheus > "Save & test", and the
"Service Health" dashboard under Dashboards.

## Ingesting a file

Migrations are applied automatically by the `migrate` service on
`docker compose up`. To ingest:

```powershell
docker compose run --rm ingestion ingest /data/inbox/messy_people.csv `
  --entity-type person --source-name vendor_x
```

Drop your own files into `data/inbox/` (bind-mounted to `/data/inbox` in the
container). Useful flags: `--record-id-column <col>` to use a natural key
instead of the row number, `--allow-reingest` to deliberately re-ingest a file
already seen, `--reliability 0.8` to record how much this source is trusted.

Exit codes: `0` ok, `2` bad input, `3` already ingested (use
`--allow-reingest`), `4` rows committed but Kafka events not published.

Inspect the result:

```powershell
docker compose exec postgres psql -U pcdf_dev -d pcdf `
  -c "SELECT status, rows_ingested, events_published_at FROM batch;"
```

> **Run these from PowerShell, not Git Bash** — Git Bash rewrites container
> paths like `/data/inbox/x.csv` into Windows paths.

### What ingestion guarantees

Source values are stored **exactly as written** — no type inference, so
leading zeros (`00501`), long IDs, `9.99E10`, unicode, and original date
formats all survive intact. Whitespace and casing are deliberately left dirty;
cleaning them is the normalizer's job in a later increment, and it needs the
original to work from.

Records are committed to Postgres *before* their Kafka events are published,
so an event can never reference a row that doesn't exist. **Postgres is the
source of truth; Kafka is a derived notification.** If Kafka is down, the rows
still land and `batch.events_published_at` stays NULL so the gap is visible.

## Schema mapping

Mapping runs **automatically**: the `mapping` service consumes
`batch.ingested` and maps each new batch's columns to canonical fields. No
manual step is needed after ingesting.

Each mapping records how it was decided (`exact_alias`, `similarity`,
`value_analysis`, `corroborated`, `manual`), a confidence, and evidence.
Confidence ≥0.90 auto-accepts; 0.60–0.90 needs review; below that the column
is recorded as unmapped rather than force-fit into a plausible-looking field.

Two rules keep it honest: **value analysis alone never auto-accepts** (a
column of valid emails could be `work_email` or `personal_email` — the values
prove the type, not the field), and **when two columns claim the same field,
both go to review** instead of the higher score silently winning.

```powershell
# See what needs a human decision
docker compose run --rm mapping review-mappings list --status needs_review

# Correct one (survives future re-ingests of the same column layout)
docker compose run --rm mapping review-mappings set --mapping-id <id> `
  --canonical-field person_external_id --reviewed-by you

# Valid canonical fields
docker compose run --rm mapping review-mappings fields --entity-type person

# Map a batch by hand (normally unnecessary)
docker compose run --rm mapping map-schema --batch-id <id> --show
```

Mappings are stored against a **source schema** (a column layout), not a
batch, so a human correction is reused the next time that source sends the
same columns.

## Normalization

Also automatic: the `normalization` service consumes `schema.mapped` and writes
one **attribute observation** per source column per row.

The governing rule is that **nothing is lost**. Every column produces an
observation — including columns that mapped to nothing, because vendor files
routinely put real data there (`FAX_NUMBER`, `SIC_DESCRIPTION`, the source's own
`ID`). `raw_value` and `normalized_value` are always stored together, and rows
are only ever appended, never overwritten. When the same entity arrives from two
vendors, both sets of attributes survive and the normalized values are what let
them be linked.

Normalization only makes values *comparable*; it never repairs or judges:

- `WASHINGTON` and `Washington` → `Washington` (otherwise they'd never match)
- `+91 (98765)-43210` → `+919876543210`, but bare `18003514494` keeps its digits
  — inventing a `+1` would fabricate provenance the source never gave
- `NULL` / `N/A` / `-` → no value, but the raw string is kept verbatim
- `A^^B` in one cell → two observations, not one mangled string
- addresses get whitespace collapsing only — `#610` and `Ave NW` carry meaning,
  and `1720WisconsinAveNW` is passed through rather than "corrected"

```powershell
# Normally unnecessary; the consumer does this automatically
docker compose run --rm normalization normalize --batch-id <id> --show 20
```

## Validation

Also automatic: the `validation` service consumes `records.normalized` and asks
one question per value — **is this usable as the field it was mapped to?**

It never repairs a value and never deletes a record. Judgements are attached
alongside the observation, which stays untouched. Three severities, and the
difference decides what happens next:

- `error` — the value cannot serve as this field at all (`not-an-email`), or the
  row has no identifying attribute and could never be resolved to an entity.
  The record becomes **`invalid`**, which means *not safe to resolve yet* — not
  *delete me*. Every raw record and observation survives.
- `warning` — plausible but suspect: a phone with no country code, `employees`
  written as `50-100`, a role mailbox (`info@`) on a *person* record.
- `info` — recorded, no judgement implied.

Two details worth knowing:

- **Passes are stored too.** Otherwise "no row" would be ambiguous between *the
  rule passed* and *the rule never ran* — and the second is common, since
  addresses, identifiers, place names and free text have no rules on purpose.
  `record_validation.unvalidated_attributes` keeps that gap countable.
- **The raw value is checked where normalization can hide a problem.**
  `employees = 50-100` normalizes to `50100`, which passes a headcount range
  check while meaning something the source never said; `integer.input` flags the
  distortion, and both judgements are kept.

```powershell
# Normally unnecessary; the consumer does this automatically
docker compose run --rm validation validate --batch-id <id> --show 20

# What is currently not safe to resolve, and why
docker compose exec postgres psql -U pcdf_dev -d pcdf `
  -c "SELECT status, count(*) FROM record_validation GROUP BY 1;"
docker compose exec postgres psql -U pcdf_dev -d pcdf `
  -c "SELECT rule_id, severity, count(*) FROM validation_result
      WHERE outcome='fail' GROUP BY 1,2 ORDER BY 3 DESC;"
```

`data/inbox/invalid_company.csv` is a deliberately broken fixture: each row
fails exactly one rule path (bad email, no identifier, impossible headcount,
future founding year, an all-`NULL` row, a 3-digit phone, a `50-100` range).

## Running tests

Polars, psycopg's binary driver, and confluent-kafka are all native
extensions, and this host's Application Control policy blocks them outside
Docker — so tests run in a container:

```powershell
docker compose run --rm ingestion python -m pytest tests -v
docker compose run --rm mapping python -m pytest tests -v
docker compose run --rm normalization python -m pytest tests -v
docker compose run --rm validation python -m pytest tests -v
```

## Future increments

Quarantine, entity resolution, golden records, and history/provenance land as
new `services/*` and `libs/common/` modules — this layout accommodates them
without restructuring. Quarantine is next: `invalid` is recorded today but
nothing yet acts on it.
