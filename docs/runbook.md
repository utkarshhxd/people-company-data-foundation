# Runbook

Operational procedures for the People & Company Data Foundation. Written for
someone who did not build it.

Every command runs from the repository root. **Use PowerShell, not Git
Bash** — Git Bash rewrites container paths like `/data/inbox/x.csv` into
Windows paths.

---

## Where things are

| | |
|---|---|
| Postgres | `localhost:5433` (**not** 5432), db `pcdf`, user `pcdf_dev` |
| Data | Docker volume `pcdf_postgres-data` |

> **`docker compose down -v` destroys every record.** The `-v` removes the
> volumes. `docker compose down` on its own is safe.

---

## Is it healthy?

```powershell
docker compose ps                                  # postgres, migrate (Exited 0), watcher
docker compose exec postgres pg_isready -U pcdf_dev -d pcdf
docker compose logs watcher --tail 50
```

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
refuses to report success until `pg_restore` has parsed what it just wrote —
the failure that matters is a backup that ran and produced something
unrestorable, silent until it's needed.

### Verify a backup — do this, not just the backup

```powershell
docker compose exec -T postgres sh /tools/ops/restore_check.sh /tmp/backups/pcdf-<stamp>.dump
```

Restores into a scratch database, compares every table's row count against
the live one, checks the views came back, drops the scratch. Safe any day —
never touches the live database.

### Restore for real

Only after `restore_check.sh` has passed on the file you intend to use.

```powershell
docker compose stop watcher
docker compose exec -T postgres psql -U pcdf_dev -d postgres -c "DROP DATABASE pcdf;"
docker compose exec -T postgres psql -U pcdf_dev -d postgres -c "CREATE DATABASE pcdf;"
docker compose exec -T postgres pg_restore -U pcdf_dev -d pcdf --exit-on-error --no-owner /tmp/backups/pcdf-<stamp>.dump
docker compose up -d
```

`watcher` is stopped first because it would otherwise write into a database
being replaced underneath it.

---

## Loading data

Two ways in. For anything that arrives more than once, prefer the first.

**A watched feed** — write the arguments once, then drop files:

```bash
mkdir -p data/inbox/watch/vendor_x
cat > data/inbox/watch/vendor_x/feed.json <<'JSON'
{"entity_type": "person", "source_name": "vendor_x",
 "reliability": 0.8, "describes": "organisation"}
JSON
docker compose up -d watcher
docker compose logs -f watcher
```

Files are left alone until they stop changing, then processed and moved to
`_done/` with a receipt — or `_failed/` with the reason, without stopping
the feed. See [data/inbox/watch/README.md](../data/inbox/watch/README.md).

**Before dropping a file**, confirm two things or the batch quarantines or
mis-types silently:

1. Row 1 of the file is a real header row, not data.
2. `entity_type` in `feed.json` matches what the rows actually describe.

**One file, by hand:**

```powershell
docker compose run --rm pipeline process run /data/inbox/file.csv `
  --entity-type person --source-name vendor_x --reliability 0.8 `
  --describes organisation
```

**`--describes` decides what a shared domain means**, set once per source:

- `organisation` — rows name companies or employers. A shared domain means
  the same company, so it can carry a link on its own.
- `location` — rows name premises. A shared domain means only the same
  brand: hundreds of franchise locations can share one domain without being
  one place. Domain and published email are demoted so they can't merge
  distinct premises; the vendor's own per-listing id stays decisive.

Defaults to `organisation`; omitting the flag on a later load leaves
whatever the source already had rather than resetting it.

Exit codes: `0` fine · `2` bad input · `3` already loaded (use
`--allow-reingest`) · `5` finished, some rows failed — not a crash, check
`process errors` · `6` validation is paused, so nothing was loaded and the
file is untouched · `7` stopped part-way because validation paused itself —
see [Validation stopped itself](#validation-stopped-itself).

For a large file, sample it first — a layout is proven by a sample, only
volume needs the whole file:

```powershell
docker compose run --rm pipeline python /tools/fixtures/sample_file.py /tf/big.xlsx /data/inbox/samples --rows 1000
```

---

## When something is wrong

### A batch is stuck `running`

A process died mid-load. The rows it wrote are complete and stay; the batch
just never got marked finished, and the duplicate guard blocks re-loading
that file until it's released.

```powershell
docker compose run --rm pipeline process abandon <batch-id>
```

Only ever marks it `failed`. Never deletes rows.

### Rows failed to process

```powershell
docker compose run --rm pipeline process errors
```

Payloads are kept byte-exact. Once the cause is fixed, replay them:

```powershell
docker compose run --rm pipeline process reprocess --batch-id <id> --reviewed-by you
```

`record_error` is **not** quarantine. Quarantine holds records the pipeline
understood and judged unusable; `record_error` holds records it couldn't
process at all — one is a verdict about the data, the other is a failure of
ours.

### Validation stopped itself

Validation stops when almost everything coming through it is invalid — the
shape of a changed vendor format, a wrong mapping, or a rule that tightened.
Nothing has been lost when this happens: records already validated kept their
verdicts and their quarantine reasons, queued work is waiting in its topic, and
files sit untouched in their feed directories. What has stopped is anything new
starting.

```powershell
docker compose run --rm validation validation-control status
```

That prints why, who or what stopped it, and the counts that caused it —
including which rule failed on most records, which is usually the answer.

Work out whether the data or the rules changed. The dominant rule points at it:
`record.has_identifier` on everything usually means the file's columns stopped
mapping (a renamed header, or a header row that isn't one); a specific rule like
`email.syntax` on everything usually means one column now holds something else.

```powershell
docker compose run --rm validation validation-control history
docker compose exec -T postgres psql -U pcdf_dev -d pcdf -c "
SELECT reason_codes, count(*) FROM quarantine_item
WHERE status='open' GROUP BY 1 ORDER BY 2 DESC LIMIT 5;"
```

Then start it again. Backlogs drain on their own from there, and a file left in
a watched feed loads on the next sweep:

```powershell
docker compose run --rm validation `
  validation-control resume --reviewed-by you --note "what you found"
```

A file that stopped part-way is marked `failed` with the reason, and its rows
stay. Re-run it with `--allow-reingest` once the cause is fixed.

**To stop it yourself**, before or during an investigation:

```powershell
docker compose run --rm validation `
  validation-control pause --reason "why" --reviewed-by you
```

The reason is required. Whoever finds the pipeline stopped is rarely the person
who stopped it.

### A whole batch quarantined and it wasn't supposed to

Two usual causes, both silent until you check:

- **The file has no real header row** — mapping had nothing to work from,
  everything landed `needs_review`/`unmapped`, so no row could satisfy
  `record.has_identifier`. Fix the file, re-drop it.
- **The mapping is right but stuck at `needs_review`** — approve it, then
  re-run normalize/validate for the batch; rows that were only blocked by
  the mapping close themselves as `resolved`:

  ```powershell
  docker compose run --rm mapping       review-mappings set --mapping-id <id> --canonical-field company_name --reviewed-by you
  docker compose run --rm normalization normalize --batch-id <id>
  docker compose run --rm validation    validate  --batch-id <id>
  ```

Check which it is:

```powershell
docker compose exec -T postgres psql -U pcdf_dev -d pcdf -c "
SELECT reason_codes, count(*) FROM quarantine_item WHERE batch_id='<id>' GROUP BY 1;"
docker compose exec -T postgres psql -U pcdf_dev -d pcdf -c "
SELECT source_column, canonical_field, mapping_status FROM column_mapping ORDER BY canonical_field NULLS LAST;"
```

### The database is filling the disk

Observations dominate: roughly one row per source column per record, about
0.9 KB each. 190,000 records of a 52-column file is ~10 GB.

```sql
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;
```

Benchmark or demo data can be removed by source name — and only by source
name:

```powershell
docker compose run --rm pipeline python /tools/ops/purge_source.py <source_name>
```

> Nothing else in the system deletes source data, by design. Invalid records
> must not disappear. Take a backup first.

---

## Work that needs a human

```sql
SELECT 'mapping needs review' AS queue, count(*) FROM column_mapping WHERE mapping_status='needs_review'
UNION ALL SELECT 'quarantined records', count(*) FROM quarantine_item WHERE status='open'
UNION ALL SELECT 'possible duplicates', count(*) FROM match_candidate WHERE status='open'
UNION ALL SELECT 'failed rows', count(*) FROM record_error WHERE status='open';
```

None of these are errors — they're decisions the system deliberately refused
to make on its own. Nothing ages a queue out on its own, so run this
periodically rather than assuming silence means empty. See
[the review queues guide](guides/review-console.md) for each queue's
commands and, critically, the follow-through step each decision does *not*
do on its own.

---

## Answering "why does this say that?"

```powershell
docker compose run --rm golden golden explain --entity-id <id> --field address_line1
```

Walks a trusted value back to the source cell: which vendor, which file,
which row, which column, what it literally said — five confidence
dimensions shown side by side, never summed. `tools/sql/queries.sql` has ten
worked examples for pgAdmin.

---

## Changing the rules

Golden records are derived, never authored — changing a survivorship or
confidence rule needs no migration, only a recalculation:

```powershell
docker compose run --rm pipeline python /tools/ops/rebuild_golden.py --dry-run
docker compose run --rm pipeline python /tools/ops/rebuild_golden.py
```

Rebuilding is idempotent: an unchanged value keeps its `valid_from`.

Changing the **canonical schema** is different — bump
`CANONICAL_SCHEMA_VERSION` in `libs/common/src/common/canonical.py`. Stored
mappings record the version they were made against, so old ones stay valid
for their version and new files get the new vocabulary.

### Backfilling batches after a schema change

New files pick up the new vocabulary on their own. Batches already loaded
don't — their observations still carry the old mapping.

```powershell
docker compose run --rm mapping       map-schema --batch-id <id>
docker compose run --rm normalization normalize  --batch-id <id>
docker compose run --rm validation    validate   --batch-id <id>
docker compose run --rm golden        golden build --batch-id <id>
```

Each stage reads what the previous one wrote — the order isn't optional.
`map-schema` derives a **new** source schema rather than editing the old
one; human corrections made against the previous version don't carry over —
check `review-mappings list --status needs_review` afterwards.

**Resolution is deliberately absent from that list.** Re-run it only if the
change touched an identity key — re-resolving a large batch for a
non-identity field risks churning links for no gain.

---

## Exposing this beyond localhost

Not something this deployment is built for. Postgres is the only thing with
a published port, and it should stay reachable only from wherever runs the
CLIs in this runbook. If that changes:

1. **Use the secrets overlay** instead of `.env` for the Postgres password —
   keeps the credential out of the container environment and
   `docker inspect`.
2. **Put a real secret store behind the seam.** `/run/secrets/<name>` is
   read without configuration, so Vault or a cloud KMS that projects files
   needs no application change. Today those files are files on a disk.
3. **Do not expose Postgres itself to anything untrusted.** What it holds is
   personal data with provenance attached; anything that needs it should go
   through the CLIs, not a direct connection.
