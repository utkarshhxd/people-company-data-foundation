# People & Company Data Foundation

Turns messy vendor exports of people and companies into trusted, deduplicated
records — without ever throwing away what a source actually said.

Drop a CSV or Excel file in a watched folder. It gets normalized, validated,
matched against everything already known, and folded into a **golden record**
per person/company. Every value stays traceable back to the exact source cell
it came from, forever.

New here? Read [Getting started](docs/guides/getting-started.md) first, then
[Processing files](docs/guides/processing-files.md).

---

## The guides

| Guide | What it covers |
| --- | --- |
| [Getting started](docs/guides/getting-started.md) | First boot, and proving the stack is actually up |
| [Processing files](docs/guides/processing-files.md) | Dropping a file, loading one by hand, large files, duplicates |
| [Schema mapping](docs/guides/schema-mapping.md) | Which canonical field a source column represents, and the human review path |
| [Normalization, validation and quarantine](docs/guides/normalization-and-validation.md) | Making values comparable, judging usability, holding back what isn't safe |
| [Entity resolution](docs/guides/entity-resolution.md) | Linking records across vendors to one real person/company |
| [Golden record and provenance](docs/guides/golden-record.md) | The one trusted value per field, traced back to its source cell |
| [Review queues](docs/guides/review-console.md) | Working the four queues that stop and ask a human, in a browser or a terminal |
| [Pipeline dashboard](docs/guides/dashboard.md) | Batch progress and how far records got, in a browser — `/admin/page` combines this with the review queues in one tabbed page |
| [AI assistance](docs/guides/ai-assistance.md) | The local-model fallback for schema mapping, and the batch enrichment job |
| [Watched feeds](data/inbox/watch/README.md) | Drop a file, it loads itself |
| [Operations](docs/guides/operations.md) | Backup/restore, CI, tests, `tools/` |
| [Runbook](docs/runbook.md) | What to do when something is wrong |

Every design decision is written down in [`docs/decisions/`](docs/decisions/)
as a numbered ADR — the *why*. These guides are the *how*, kept in sync with
what the code actually does today.

## The shape of it

```
docker-compose.yml       postgres, kafka, migrate job, five stage consumers,
                         the watcher, the review console
db/migrations/           numbered .sql migrations, applied by `migrate`
libs/common/             settings, db, migration runner, canonical schema

services/record_pipeline/  the automatic path: one record, start to finish
                            + the watcher that drives it off dropped files
services/ingestion/        CSV/Excel → raw rows (used by the manual/reprocess path)
services/mapping/          source column → canonical field
services/normalization/    raw value → comparable value
services/validation/       is this value usable? quarantine what isn't
services/resolution/       link records to a stable person_id / company_id
services/golden/           one trusted value per entity per field
services/review_console/   browser console for the four review queues

tools/ops/                run against a live stack: backup, restore, purge
tools/fixtures/           generate or cut sample data
tools/sql/                hand-verification queries

data/inbox/watch/         one directory per feed; drop files in, they load
```

**Everything runs in Docker.** No native install to manage. `postgres`,
`kafka`, `migrate`, the five stage consumers, `watcher` and `review_console`
start with the stack; `ingestion`, `pipeline` and `enrichment` are CLI-only,
invoked with `docker compose run --rm <service> ...`. Every stage's CLI stays
reachable that way too — the consumer is only its default command.

## Two ways a record gets processed — same rules either way

- **Drop a file in a watched feed, or run `pipeline process run` by hand.**
  One record goes normalize → validate → quarantine-if-needed → resolve →
  golden, start to finish, before the next record starts. This is the path
  for loading a file, and it's genuinely automatic — nothing to trigger by
  hand once the file lands.
- **The stage CLIs** (`map-schema`, `normalize`, `validate`, `resolve`,
  `golden build`), each against a batch already in Postgres by `--batch-id`,
  chained by hand. This is the path for *reprocessing* — redoing one stage
  after a rule change, without touching the source file again. Running
  `ingest` instead of `process run` puts a batch on this path and the
  consumers chain the stages for you; the CLIs are for when you want one
  stage and only one.

Both call the same underlying functions, so they can never disagree about
what a record means. See [ADR 0012](docs/decisions/0012-record-at-a-time-processing.md).

**Kafka carries announcements, never data.** A record moving between stages
inside the record-at-a-time path is a function call in one transaction, and a
batch stage reads its rows out of Postgres — no value ever travels on a queue.
What does travel is a reference: `batch.ingested` says a batch is committed and
ready, and the five stage consumers work from it. Postgres stays the source of
truth, so an event can go stale against nothing.

That indirection is what stops one stopped service costing anything. Take
`resolution` down and its work accumulates in `pcdf.records.validated`; offsets
are committed only after a batch is processed, so starting it again picks up
exactly where it left off. Rows are committed *before* anything is published,
which means an event can never point at a row that does not exist — and when
the broker is unreachable, `batch.events_published_at` stays NULL so the gap is
queryable instead of silent.

## Where AI is, and isn't, allowed

**AI may propose, never assert.** Concretely:

- Allowed: suggesting a canonical field for a column stuck in `needs_review`;
  judging an ambiguous duplicate pair for a human to confirm; normalizing a
  job title into a taxonomy; parsing an address a deterministic parser
  couldn't.
- Not allowed: producing facts. A model asked for a company's revenue will
  answer fluently and often wrongly, indistinguishable afterwards from a
  value a real source actually stated. In a system whose entire value is
  that every value traces back to who claimed it, that's contamination.
- **Nothing writes to `golden_attribute` without an observation behind it.**
  Real enrichment (registries, WHOIS, paid data) enters as its own `source`
  row, competing as evidence like any vendor — never as an AI-authored fact.

Full reasoning: [ADR 0011](docs/decisions/0011-increment-11-review-findings.md),
[ADR 0014](docs/decisions/0014-commercial-profile-and-enrichment.md).

## How it was built

| # | Increment | ADR |
| --- | --- | --- |
| 1 | Local infrastructure: Compose, PostgreSQL, health checks | [0001](docs/decisions/0001-increment-1-infra.md) |
| 2 | Ingestion: read messy CSV/Excel, record every row verbatim with provenance | [0002](docs/decisions/0002-increment-2-ingestion.md) |
| 3 | Schema mapping: which canonical field each column is, with method, confidence, evidence and a review path | [0003](docs/decisions/0003-increment-3-schema-mapping.md) |
| 4 | Normalization and lossless capture: every column of every row becomes an observation | [0004](docs/decisions/0004-increment-4-normalization.md) |
| 5 | Validation: is this value usable, and could this record ever resolve to an entity | [0005](docs/decisions/0005-increment-5-validation.md) |
| 6 | Quarantine: hold invalid records back without losing them, with a human path in and out | [0006](docs/decisions/0006-increment-6-quarantine.md) |
| 7 | Entity resolution: stable `person_id`/`company_id`, cross-vendor linking, merges that never destroy an id | [0007](docs/decisions/0007-increment-7-entity-resolution.md) |
| 8 | Golden record: the single trusted value per entity per field, with the deciding rule and rejected alternatives | [0008](docs/decisions/0008-increment-8-golden-record.md) |
| 9 | Provenance: trace any trusted value back to its source cell | [0009](docs/decisions/0009-increment-9-provenance-api.md) |
| 10 | Pipeline metrics: make the quiet failure modes visible | [0010](docs/decisions/0010-increment-10-pipeline-metrics.md) |
| 11 | Review findings: streamed batch ingestion, validation split per concern, where AI is/isn't allowed | [0011](docs/decisions/0011-increment-11-review-findings.md) |
| 12 | Record-at-a-time processing: a record's fate stops depending on the rows it shared a file with | [0012](docs/decisions/0012-record-at-a-time-processing.md) |
| 13 | The employer named in a person row becomes a company entity, linked by `employed_at` | [0013](docs/decisions/0013-employer-as-an-entity.md) |
| 14 | The commercial profile: captured-but-ignored vendor columns get a canonical home; where enrichment is allowed to live | [0014](docs/decisions/0014-commercial-profile-and-enrichment.md) |
| 15 | Operable by someone else: a review console, feeds that load themselves, credentials from files | [0015](docs/decisions/0015-operable-by-someone-else.md) |
| 16 | Kubernetes manifests: same pieces, across a cluster | [0016](docs/decisions/0016-kubernetes-deployment.md) |
| 17 | AI schema mapping and enrichment, both against a local model, both only ever proposing | [0017](docs/decisions/0017-ai-schema-mapping-and-enrichment.md) |
| 18 | The broker and the monitoring stack come back — announcements, not values, and a stopped stage becomes a queue | [0018](docs/decisions/0018-restoring-the-broker-and-monitoring.md) |

Every ADR is kept even after the code it describes changes — it's the record
of *why*, not a promise the description still matches today's code. These
guides are what matches today's code; check here first.
