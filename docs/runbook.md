# Runbook

Operational procedures for the People & Company Data Foundation. Written for
someone who did not build it.

Every command runs from the repository root. **Use PowerShell, not Git Bash** —
Git Bash rewrites container paths like `/data/inbox/x.csv` into Windows paths.

---

## Where things are

| | |
|---|---|
| Postgres | `localhost:5433` (**not** 5432), db `pcdf`, user `pcdf_dev` |
| API | http://localhost:8000 — docs at `/docs` |
| Grafana | http://localhost:3001 — "Data Foundation" dashboard |
| Prometheus | http://localhost:9090 — rules under Status > Rules |
| Alerts page | http://localhost:8000/alerts/page — what is firing, as a page |
| Alertmanager | http://localhost:9093 — the engine behind it |
| Data | Docker volume `pcdf_postgres-data` |

Postgres is on 5433 so it cannot clash with a native install on 5432. Grafana is
on 3001 because 3000 is the port every Node dev server wants; when it is taken,
Docker Desktop can leave the container up and healthy but unreachable from the
host rather than failing loudly.

> **`docker compose down -v` destroys every record.** The `-v` removes the
> volumes. `docker compose down` on its own is safe.

---

## Is it healthy?

```powershell
docker compose ps                                   # every service (healthy)
curl.exe -s http://localhost:8000/health/ready      # checks Postgres + Kafka
curl.exe -s http://localhost:8000/metrics | Select-String "^pcdf_"
```

`pcdf_metrics_up 0` means the API is running but cannot reach Postgres. The
endpoint deliberately still returns 200 with no stale gauges — monitoring that
dies with the database is useless exactly when it is needed.

**Check the data is internally consistent** — twelve reconciliation checks,
each returning rows only on failure, so all-empty is the pass:

```powershell
docker compose cp tools/sql/verify.sql postgres:/tmp/verify.sql
docker compose exec -T postgres psql -U pcdf_dev -d pcdf -f /tmp/verify.sql
```

---

## Backup and restore

### Take a backup

```powershell
docker compose exec -T postgres sh /tools/ops/backup.sh /tmp/backups
docker compose cp postgres:/tmp/backups ./backups     # copy off the container
```

Custom format, so it compresses and can be restored selectively. The script
refuses to report success until `pg_restore` has parsed what it just wrote: the
failure that matters is not a backup that did not run, which is noisy, but one
that ran and produced something unrestorable, which is silent until it is
needed.

### Verify a backup — do this, not just the backup

```powershell
docker compose exec -T postgres sh /tools/ops/restore_check.sh /tmp/backups/pcdf-<stamp>.dump
```

Restores into a scratch database, compares every table's row count against the
live one, checks the views came back, then drops the scratch. Safe to run any
day — it never touches the live database. `--keep` leaves the scratch for
inspection.

### Restore for real

Only after `restore_check.sh` has passed on the file you intend to use.

```powershell
docker compose stop api mapping normalization validation resolution golden
docker compose exec -T postgres psql -U pcdf_dev -d postgres -c "DROP DATABASE pcdf;"
docker compose exec -T postgres psql -U pcdf_dev -d postgres -c "CREATE DATABASE pcdf;"
docker compose exec -T postgres pg_restore -U pcdf_dev -d pcdf --exit-on-error --no-owner /tmp/backups/pcdf-<stamp>.dump
docker compose up -d
```

Consumers are stopped first because they would otherwise write into a database
that is being replaced underneath them.

---

## Loading data

```powershell
docker compose run --rm pipeline process run /data/inbox/file.csv `
  --entity-type person --source-name vendor_x --reliability 0.8 `
  --describes organisation
```

**`--describes` is set once per source and decides what a shared domain means.**

- `organisation` — rows name companies or employers. A shared domain means the
  same company, so it can carry a link on its own. Apollo and similar
  contact/employer feeds.
- `location` — rows name premises. A shared domain means only the same brand:
  156 Subway franchises share `subway.com`, every branch of the US Post Office
  shares `usps.com`, and 442 separate agencies share `maine.gov`. Domain and
  published email are demoted so they cannot merge distinct places; the
  vendor's own per-listing id stays decisive.

It defaults to `organisation`, and omitting the flag on a later load leaves
whatever the source already had rather than resetting it.

Exit codes: `0` fine · `2` bad input · `3` already loaded (use
`--allow-reingest`) · `5` finished, but some rows failed.

**Exit 5 is not a crash.** It means the run processed what it could and recorded
the rest. Check what failed with `process errors`. A scheduled load that quietly
drops rows is how data goes missing, so a partially successful run still exits
non-zero.

For a large file, sample it first — a layout is proven by a sample; only volume
needs the whole file:

```powershell
docker compose run --rm pipeline python /tools/fixtures/sample_file.py /tf/big.xlsx /data/inbox/samples --rows 1000
docker compose run --rm pipeline python /tools/measure/mapping_coverage.py   # what needs review first
```

---

## When something is wrong

### A batch is stuck `running`

A process died mid-load. The rows it wrote are complete and stay; the batch just
never got marked finished, and the duplicate guard will block re-loading that
file until it is released.

```powershell
docker compose run --rm pipeline process abandon <batch-id>
```

Only ever marks it `failed`. It never deletes rows.

### Rows failed to process

```powershell
docker compose run --rm pipeline process errors
```

Payloads are kept byte-exact. Once the cause is fixed, replay them:

```powershell
docker compose run --rm pipeline process reprocess --batch-id <id> --reviewed-by you
```

`record_error` is **not** quarantine. Quarantine holds records the pipeline
understood and judged unusable. `record_error` holds records it could not
process at all — one is a verdict about the data, the other is a failure of
ours.

### A consumer is not keeping up, or is stuck

```powershell
docker compose logs mapping --tail 50
docker compose restart mapping
```

Offsets commit only after work completes, so a restart reprocesses at most the
in-flight batch. Every stage is idempotent; replaying writes nothing new.

### The database is filling the disk

Observations dominate: roughly one row per source column per record, about
0.9 KB each. 190,000 records of a 52-column file is ~10 GB.

```sql
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;
```

Benchmark or demo data can be removed by source name — and only by source name:

```powershell
docker compose run --rm pipeline python /tools/ops/purge_source.py <source_name>
```

> Nothing else in the system deletes source data, by design. Invalid records
> must not disappear. Take a backup first.

---

## Alerts

Rules live in `infra/prometheus/rules/alerts.yml`; routing in
`infra/alertmanager/alertmanager.yml`. Firing alerts collect at
http://localhost:9093 and nothing leaves the machine until a webhook is
configured — a destination is something somebody has to own, and a webhook URL
committed to a repository is a credential committed to a repository.

**Almost every rule alerts on age, not depth.** A queue of three is fine; three
that have been the same three for a fortnight means somebody stopped reviewing,
and no count reveals that. A depth threshold either fires constantly on a busy
day or never fires at all.

| Alert | Fires when |
| --- | --- |
| `MetricsCannotReachDatabase` | the collector cannot query Postgres — every gauge below is stale |
| `ApiDown` | Prometheus cannot scrape the API |
| `RecordsCommittedButNotPublished` | rows landed but Kafka events did not, so no stage will run |
| `BatchFailed` | a batch failed in the last hour |
| `RecordsFailedToProcess` | validated records never reached an entity — a consumer may be stopped |
| `QuarantineNotBeingReviewed` | oldest quarantined record > 7 days |
| `MappingsNotBeingReviewed` | oldest unreviewed mapping > 7 days |
| `MatchCandidatesNotBeingReviewed` | oldest possible-duplicate > 14 days |
| `MatchCandidatesGrowingFast` | > 500 new candidates in an hour — a source producing systematic ambiguity |
| `RecordsBeingQuarantined` | > 100 records became invalid in an hour — a vendor format probably changed |
| `UnmappedColumnsHigh` | > 75% of observations map to no field |

`MetricsCannotReachDatabase` and `ApiDown` inhibit the warnings beneath them: if
the collector is down, the queue gauges are simply old, and reporting the
symptoms alongside the cause is how an incident becomes noise.

**Seeing what is firing** without leaving the app: http://localhost:8000/alerts/page,
backed by `GET /alerts`. Both are read-only projections of what Alertmanager
holds — the rules, grouping, inhibition and resolution all still happen in
Prometheus and Alertmanager. Nothing about a notification channel is encoded in
the API, so adding Slack or email later is a change to Alertmanager's routing
and touches no application code.

The endpoint returns **503 rather than an empty list** when Alertmanager cannot
be reached: "nothing is wrong" and "we cannot tell whether anything is wrong"
are opposite states, and a page that renders them identically is worse than one
showing an error.

**To deliver somewhere**, uncomment the webhook receiver in
`infra/alertmanager/alertmanager.yml` and set `ALERTMANAGER_WEBHOOK_URL`. Slack,
Teams and Discord all accept that shape.

**To test a rule fires**, lower its threshold, `docker compose restart
prometheus`, and check both http://localhost:9090/alerts and
http://localhost:9093 — then put the threshold back. An alert rule that has
never fired is as likely to be wrong as right.

## Work that needs a human

```sql
SELECT 'mapping needs review' AS queue, count(*) FROM column_mapping WHERE mapping_status='needs_review'
UNION ALL SELECT 'quarantined records', count(*) FROM quarantine_item WHERE status='open'
UNION ALL SELECT 'possible duplicates', count(*) FROM match_candidate WHERE status='open'
UNION ALL SELECT 'failed rows', count(*) FROM record_error WHERE status='open';
```

None of these are errors. They are the decisions the system deliberately
refused to make on its own.

**The age of a queue matters more than its depth.** A backlog of three is fine;
a backlog of three that has been three for a fortnight is a process failure, and
no count alone reveals that. Alert on
`pcdf_oldest_open_quarantine_age_seconds` and
`pcdf_oldest_unreviewed_mapping_age_seconds`, not on the counts.

### Approve a column mapping

```powershell
docker compose run --rm mapping review-mappings list --status needs_review
docker compose run --rm mapping review-mappings set --mapping-id <id> `
  --canonical-field person_external_id --reviewed-by you
```

Stored against the column layout, not the file, so the correction is reused
every future time that source sends the same columns.

### Release or reject a quarantined record

```powershell
docker compose run --rm validation quarantine show --record-id <id>
docker compose run --rm validation quarantine release --record-id <id> --reviewed-by you --note "why"
docker compose run --rm validation quarantine reject  --record-id <id> --reviewed-by you --note "why"
```

Releasing does not re-trigger resolution. Re-run it for that batch:

```powershell
docker compose run --rm resolution resolve run --batch-id <id>
```

### Accept or reject a possible duplicate

```powershell
docker compose run --rm resolution resolve candidates
docker compose run --rm resolution resolve accept --candidate-id <id> --reviewed-by you --note "why"
```

Accepting merges. **Merging never deletes an id** — the absorbed entity becomes
a tombstone pointing at the survivor, so any id already handed out still
resolves. Rebuild the survivor's golden values afterwards:

```powershell
docker compose run --rm golden golden build --entity-id <surviving-id>
```

---

## Answering "why does this say that?"

```powershell
curl.exe "http://localhost:8000/entities/<id>/explain/company_name"
docker compose run --rm golden golden explain --entity-id <id> --field address_line1
```

Walks a trusted value back to the source cell: which vendor, which file, which
row, which column, and what it literally said — with all five confidence
dimensions side by side and never summed.

`tools/sql/queries.sql` has ten worked examples for pgAdmin.

---

## Changing the rules

Golden records are derived, never authored, so changing a survivorship or
confidence rule needs no migration — only a recalculation:

```powershell
docker compose run --rm pipeline python /tools/ops/rebuild_golden.py --dry-run
docker compose run --rm pipeline python /tools/ops/rebuild_golden.py
```

Rebuilding is idempotent: an unchanged value keeps its `valid_from`, so
`valid_from` means "since when has this been true", not "when did the builder
last run".

Changing the **canonical schema** is different — bump `CANONICAL_SCHEMA_VERSION`
in `libs/common/src/common/canonical.py`. Stored mappings record the version
they were made against, so old ones stay valid for their version and new files
get the new vocabulary.

### Backfilling batches after a schema change

New files pick the new vocabulary up on their own. Batches already loaded do
not: their observations still carry the mapping made against the old version,
so a field that now exists is still attributed to nothing for them.

Re-run the mapping-dependent stages, in this order, one batch at a time:

```powershell
docker compose run --rm mapping       map-schema --batch-id <id>
docker compose run --rm normalization normalize  --batch-id <id>
docker compose run --rm validation    validate   --batch-id <id>
docker compose run --rm golden        golden build --batch-id <id>
```

Each stage reads what the previous one wrote, so the order is not optional.

`map-schema` derives a **new** source schema rather than editing the old one,
because the fingerprint now includes the new version. Any human corrections made
against the previous version do not carry over — check
`review-mappings list --status needs_review` afterwards.

**Resolution is deliberately absent from that list.** Re-run it only if the
change touched an identity key. Nothing else can move a record between entities,
and re-resolving a large batch for a non-identity field risks churning links for
no gain.

Normalization and validation are both safe to re-run: observations are upserted
(`raw_value` and `observed_at` are never overwritten — they are what the source
said), and validation retracts its own previous verdicts for the current ruleset
before writing new ones, so a rule that stops firing does not leave its old
failure behind.

---

## Exposing this beyond localhost

Not ready. Before it is:

1. **Set `PCDF_API_KEYS`** and remove `PCDF_ALLOW_UNAUTHENTICATED` from `.env`.
   With neither set the API refuses non-loopback requests, which is the safe
   default but not a configuration.
2. **Move credentials out of `.env`** into a real secret store. They are
   plaintext on disk today.
3. **Add TLS.** Keys sent over plain HTTP are keys published.
4. **Route the alerts somewhere.** The rules and the Alerts page exist; no
   notification channel is wired up, so nothing reaches anyone who is not
   looking at the page. This is a receiver in `alertmanager.yml`, not an
   application change.

The API is read-only, which limits the damage but not the disclosure: what it
serves is personal data with provenance attached.
