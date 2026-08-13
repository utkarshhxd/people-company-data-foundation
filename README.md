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
- **Increment 5** — validation: judge whether each observed value is usable as
  the field it was mapped to, and whether the record could ever be resolved to
  an entity. (`docs/decisions/0005-increment-5-validation.md`)
- **Increment 6** — quarantine: hold invalid records back from entity
  resolution without losing them, with a human review path in and out.
  (`docs/decisions/0006-increment-6-quarantine.md`)
- **Increment 7** — entity resolution: assign a stable `person_id` /
  `company_id`, link records from different vendors to the same real-world
  entity, and merge without ever destroying an id.
  (`docs/decisions/0007-increment-7-entity-resolution.md`)
- **Increment 8** (this state) — golden record: decide the single trusted value
  per entity per field, with the deciding rule, the rejected alternatives, and
  full history. (`docs/decisions/0008-increment-8-golden-record.md`)

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
services/validation/     # validation consumer + validate/quarantine CLIs
services/resolution/     # entity-resolution consumer + resolve CLI
services/golden/         # golden-record consumer + golden CLI
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

## Quarantine

A record validated as `invalid` is routed to quarantine **in the same
transaction** as the verdict, so there is never a moment when a record is
known-bad but still visible downstream. Only `error`-severity failures
quarantine anything; warnings never do.

The control is the `resolvable_record` view — **entity resolution reads that,
not `raw_record`**. Quarantined records stay fully intact in `raw_record` and
`attribute_observation`; they are simply absent from the view.

```powershell
docker compose run --rm validation quarantine list --status open
docker compose run --rm validation quarantine show --record-id <id>

# Use it anyway / confirm it is unusable. Both are recorded permanently.
docker compose run --rm validation quarantine release --record-id <id> `
  --reviewed-by you --note "vendor confirmed by phone"
docker compose run --rm validation quarantine reject --record-id <id> `
  --reviewed-by you --note "row is entirely NULL placeholders"

docker compose run --rm validation quarantine stats
```

Four dispositions, and the lifecycle rules are the interesting part:

- `open` → awaiting a human.
- `released` → usable despite the failures. It survives the *same* failure
  recurring (re-running validation must not undo a person), but a **new** failure
  reopens it — they signed off on one problem, not on any future one.
- `rejected` → confirmed unusable. Terminal, and it outranks a later clean
  validation: a ruleset change must not quietly overturn a human judgement.
- `resolved` → re-validation passed, closed automatically with no human. This is
  what makes fixing a mapping cheap instead of generating review work.

Every transition is appended to `quarantine_event` with actor and reason, so a
release followed by a re-quarantine never erases who signed off.

`data/inbox/ambiguous_company.csv` demonstrates the full loop: a file with both
`name` and `org_name` collides on `company_name`, neither mapping confirms, so
the records have no identifier and are quarantined — approve one mapping,
re-normalize, re-validate, and they close themselves as `resolved`.

## Entity resolution

The point of everything above: records become observations *of* a stable
`person_id` / `company_id` that survives the attributes changing, the vendor
changing, and the record being superseded.

```powershell
# Automatic on records.validated; this re-runs a batch (e.g. after releasing
# a record from quarantine, which does not itself re-trigger resolution)
docker compose run --rm resolution resolve run --batch-id <id>

# Everything known about an entity, and which vendor said it
docker compose run --rm resolution resolve show --entity-id <id>

# Matches that were NOT acted on automatically
docker compose run --rm resolution resolve candidates
docker compose run --rm resolution resolve accept --candidate-id <id> `
  --reviewed-by you --note "same organisation"
docker compose run --rm resolution resolve reject --candidate-id <id> `
  --reviewed-by you --note "different companies, shared switchboard"
```

**Strong keys can link on their own; moderate keys never can.** Email, website
domain, LinkedIn handle and the vendor's own id are strong. Name, name+city and
phone are moderate — and no amount of moderate agreement crosses the auto-link
line, because three colleagues share an employer, a city and a switchboard.
Corroboration adds +0.04 per additional key *type*, capped, so weak signals
never impersonate a strong one.

Two more rules worth knowing:

- A vendor's own id is scoped as `{source_id}:{external_id}` — authoritative
  inside one feed, meaningless across feeds that reuse the same integers.
- A role mailbox (`info@`) is demoted for person matching, reusing validation's
  stored `email.role_account` verdict rather than re-deciding what a role
  account is.

Below the threshold the record still gets its own entity and a `match_candidate`
is filed, so the pipeline never blocks on a human and no merge is invented.
**Merging never deletes an id** — the absorbed entity becomes a tombstone
pointing at the survivor, so any id already handed out still resolves.

Resolution reads the `resolvable_record` view, so quarantined records are never
offered for matching.

### Worked example

`vendor_b_overlap.csv` linked 2 of 2 records to existing entities and created
none. The Asia Foundation is now one entity observed by three vendors — matched
on **website domain despite the names disagreeing** ("Asia Foundation" vs "The
Asia Foundation"), which a name-based match would have missed:

```
observed by 3 record(s):
  vendor_dc    real_company_sample.csv   first sighting
  vendor_b     vendor_b_overlap.csv      website_domain  0.960 (auto_linked)
  vendor_near  near_match_company.csv    name_city       0.840 (human accepted)

company_name   vendor_b  The Asia Foundation
               vendor_dc Asia Foundation
employee_count vendor_b  1200
sic_description vendor_dc Associations
...
```

Every value is still attributed to the vendor that reported it, and nothing was
overwritten.

## Golden record

The deliverable: one trusted value per entity per field, with the rule that
decided it, what it beat, and full history. It is **derived, never authored** —
recomputable at any time from `attribute_observation`.

```powershell
# Automatic on entities.resolved; re-run after accepting a merge candidate
docker compose run --rm golden golden build --entity-id <id>

docker compose run --rm golden golden show --entity-id <id>
docker compose run --rm golden golden history --entity-id <id> --field address_line1

# The flat "trusted canonical data" views
docker compose exec postgres psql -U pcdf_dev -d pcdf -c "SELECT * FROM golden_company;"
docker compose exec postgres psql -U pcdf_dev -d pcdf -c "SELECT * FROM golden_person;"
```

**One rule per field, because fields differ in kind.** An address *changes*, so
the newest report wins. A company name does not, so disagreement means someone
is wrong and votes are counted — and one vendor repeating itself is one opinion,
not many. A founding year is immutable, so a later vendor cannot know better.
Everything else trusts the most reliable source.

**Confidence comes from source reliability**: start at the winning source's
reliability, +0.15 per additional agreeing source, −0.10 per rejected competing
value. A contested field should read as less certain, and it does:

```
company_name  Asia Foundation        0.750  most_frequent         vendor_dc
                rejected: The Asia Foundation (vendor_b)
email         taf@pk.asiafound.org   0.850  most_reliable_source  vendor_dc
phone         +12025889420           0.300  most_recent           vendor_near
                rejected: +14153928863 (vendor_b)
                rejected: 4153928863 (vendor_dc)
```

That is now the **fifth** distinct confidence-like number in the system, and
they are never conflated: mapping confidence, validation result, source
reliability, match confidence, golden-value confidence.

**Current state and history are one table** (`valid_to IS NULL` means current, a
partial unique index enforces one current value per field). Rebuilding is
idempotent — an unchanged value keeps its `valid_from`, so that column means
"since when has this been true", not "when did the builder last run":

```
address_line1  1779 Massachusetts Ave NW #815  06:08 -> 06:10   vendor_dc
address_line1  465 California St 9th Floor     06:10 -> current vendor_c
```

Only records actually linked to an entity contribute, so a quarantined record
stays fully stored while backing no trusted value.

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
```

## Future increments

The pipeline is end to end: a messy CSV lands in `data/inbox/` and becomes a
trusted, provenanced, versioned canonical record with no manual step. What is
still open:

- **Provenance surfacing** (spec step 16 in full) — the data is all there
  (`attribute_observation`, `validation_result`, `record_entity_link`,
  `golden_attribute` history), but there is no single "explain this value"
  endpoint that walks the chain from golden value back to the source cell.
- **Read API** — the `api` service still only serves health checks; the golden
  views are reachable via psql only.
- **Metrics and dashboards** — Grafana is provisioned but the pipeline emits no
  business metrics (quarantine depth, open match candidates, contested golden
  fields, mapping review backlog).
- **Re-blocking** — two entities that should have merged stay separate until a
  third record matches both. Nothing re-examines old entities when new keys
  arrive.
