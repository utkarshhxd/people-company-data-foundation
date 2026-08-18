# People & Company Data Foundation

A local-first data foundation that ingests messy People/Company records,
normalizes and validates them, resolves them to real-world entities, and
stores trusted canonical data with full history and provenance.

**190,574 records in one run. 11.8M observations. Zero rows lost.**
See [Verified at scale](#verified-at-scale).

New here? Start with [Getting started](docs/guides/getting-started.md), then
[Processing files](docs/guides/processing-files.md).

---

## The guides

This README is the map. Each stage has its own guide, with the commands, the
worked examples and the reasoning for that stage.

| Guide | What it covers |
| --- | --- |
| [Getting started](docs/guides/getting-started.md) | Prerequisites, first boot, proving the stack is actually up |
| [Processing files](docs/guides/processing-files.md) | The two processing paths, ingestion guarantees, large and duplicate files, working with real vendor exports |
| [Schema mapping](docs/guides/schema-mapping.md) | Which canonical field a source column represents, and the human review path |
| [Normalization, validation and quarantine](docs/guides/normalization-and-validation.md) | Making values comparable, judging whether they are usable, holding back the ones that are not |
| [Entity resolution](docs/guides/entity-resolution.md) | Linking records across vendors, and the employer named inside a person row |
| [Golden record and provenance](docs/guides/golden-record.md) | The one trusted value per field, and tracing it back to a source cell |
| [Read API](docs/guides/read-api.md) | Serving trusted data and lineage over HTTP, and the authentication in front of it |
| [Operations](docs/guides/operations.md) | Metrics, dashboards, alerts, backup and restore, CI, tests |
| [Runbook](docs/runbook.md) | What to do when something is wrong |

Every design decision is written down in [`docs/decisions/`](docs/decisions/) as
a numbered ADR. The ADRs carry the *why*; the guides carry the *how*.

## Repository layout

```
docker-compose.yml            # the whole stack: infrastructure, stage services, api
pyproject.toml                # uv workspace root
db/migrations/                # numbered .sql migrations, applied by the `migrate` service
infra/                        # prometheus, alertmanager and grafana provisioning
libs/common/                  # shared: settings, db, migration runner, Kafka event shapes

services/ingestion/           # CSV/Excel ingestion CLI
services/mapping/             # schema-mapping consumer + map-schema/review CLIs
services/normalization/       # normalization consumer + normalize CLI
services/validation/          # validation consumer + validate/quarantine CLIs
services/resolution/          # entity-resolution consumer + resolve CLI
services/golden/              # golden-record consumer + golden CLI
services/record_pipeline/     # record-at-a-time path: one record, end to end
services/api/                 # FastAPI read API, health checks, alerts projection

tools/ops/                    # run against a live stack: backup, restore, reblock, loaders
tools/measure/                # benchmarks, parity, coverage, profiling
tools/fixtures/               # generate or cut sample data
tools/sql/                    # hand-verification queries

docs/guides/                  # how to use each stage
docs/decisions/               # numbered ADRs: why each stage is shaped as it is
docs/reports/                 # status and delivery-timeline pages for non-engineers

data/inbox/fixtures/          # synthetic files the guides refer to (committed)
data/inbox/                   # local drop dir, bind-mounted into the containers
testfiles/                    # real vendor exports (gitignored: they carry personal data)
```

Directory names match package names, and `tools/` is grouped by what a script is
*for* rather than what it is written in — an operator looking for the backup
script should not have to know it is shell.

## Two processing paths, one set of rules

There are two ways a record can be processed, and they run the same stage logic:

- **`services/record_pipeline`** takes one record the whole way through —
  normalize, validate, quarantine, resolve, golden — before starting the next.
  This is the path for loading a file. See
  [Processing files](docs/guides/processing-files.md).
- **The stage services** each run one stage across a whole batch, triggered by
  Kafka. This is the path for *reprocessing* records already in Postgres — for
  example re-validating everything after a `RULESET_VERSION` bump, which never
  touches the source file.

Neither reimplements the other: both call the same functions, so they cannot
disagree about what a record means. That is verified rather than asserted — see
`tools/measure/parity.py` and
[ADR 0012](docs/decisions/0012-record-at-a-time-processing.md).

## Verified at scale

190,574 Apollo person records, 52 columns, in one run: 2h13m, 23.9 records/sec,
9.9M observations, 190,163 people, **49,941 companies**, 189,084 employments,
zero quarantined. Nine distinct vendor layouts have been through end to end,
from 9 to 54 columns, CSV and Excel.

The full database restores from backup with every table matching — 11.8M
observations, verified rather than assumed.

## How it was built

The repository was built incrementally, and each increment has an ADR.

| # | Increment | ADR |
| --- | --- | --- |
| 1 | Local infrastructure: Compose, PostgreSQL, Kafka, Prometheus/Grafana, health checks | [0001](docs/decisions/0001-increment-1-infra.md) |
| 2 | Ingestion: read messy CSV/Excel, record every row verbatim with provenance | [0002](docs/decisions/0002-increment-2-ingestion.md) |
| 3 | Schema mapping: which canonical field each column is, with method, confidence, evidence and a review path | [0003](docs/decisions/0003-increment-3-schema-mapping.md) |
| 4 | Normalization and lossless capture: every column of every row becomes an observation, raw and normalized side by side | [0004](docs/decisions/0004-increment-4-normalization.md) |
| 5 | Validation: is this value usable as the field it was mapped to, and could this record ever resolve to an entity | [0005](docs/decisions/0005-increment-5-validation.md) |
| 6 | Quarantine: hold invalid records back from resolution without losing them, with a human path in and out | [0006](docs/decisions/0006-increment-6-quarantine.md) |
| 7 | Entity resolution: stable `person_id`/`company_id`, cross-vendor linking, merges that never destroy an id | [0007](docs/decisions/0007-increment-7-entity-resolution.md) |
| 8 | Golden record: the single trusted value per entity per field, with the deciding rule and the rejected alternatives | [0008](docs/decisions/0008-increment-8-golden-record.md) |
| 9 | Provenance and the read API: trace any trusted value back to its source cell, and serve it over HTTP | [0009](docs/decisions/0009-increment-9-provenance-api.md) |
| 10 | Pipeline metrics: make the quiet failure modes visible — review backlogs, contested values, oldest waiting item | [0010](docs/decisions/0010-increment-10-pipeline-metrics.md) |
| 11 | Review findings: streamed batch ingestion, validation rules split per concern, address rules, hardened duplicate detection | [0011](docs/decisions/0011-increment-11-review-findings.md) |
| 12 | Record-at-a-time processing: a record's fate stops depending on the rows it shared a file with | [0012](docs/decisions/0012-record-at-a-time-processing.md) |
| 13 | The employer named in a person row becomes a company entity, linked by `employed_at` | [0013](docs/decisions/0013-employer-as-an-entity.md) |
| 14 | The commercial profile: nine captured-but-ignored vendor columns get a canonical home, plus `money` and `date` value types | [0014](docs/decisions/0014-commercial-profile-and-enrichment.md) |

## Future increments

- **Re-blocking** — two entities that should have merged stay separate until a
  third record matches both. Nothing re-examines old entities when new keys
  arrive.
- **Secrets** — credentials are plaintext in `.env`. Non-root containers and API
  keys are done; a real secret store is not.
- **Re-blocking with source semantics** — `source.describes` now tells
  resolution whether a shared domain means the same company or merely the same
  brand. Entities created before it was set were resolved without it, so the
  duplicates already in the database still need a pass; `tools/ops/reblock.py`
  reports them.
