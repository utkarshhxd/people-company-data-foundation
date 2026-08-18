# Processing files

The two paths a file can take through the system, and what each guarantees.

Part of the [People & Company Data Foundation](../../README.md).

## Processing a file, record at a time

Migrations are applied automatically by the `migrate` service on
`docker compose up`. To load a file:

```powershell
docker compose run --rm pipeline process run /data/inbox/fixtures/messy_people.csv `
  --entity-type person --source-name vendor_x
```

Each record is normalized, validated, quarantined or resolved to an entity, and
its entity's golden values rebuilt, before the next record starts. When a record
finishes, everything derived from it is current — which is what makes it safe to
hand to another system immediately. One `pcdf.record.processed` event is emitted
per record, carrying references and status, never values.

```
batch 01a0008a-37ac-7382-911e-611922218553
  read       10
  processed   8
  failed      2
  invalid     0 (quarantined 0)
  linked      3
  new         5
  review      0
  mapping    reused
```

**A record that cannot be processed fails alone.** It is rolled back, written to
`record_error` with its payload and its error, and the run continues — one bad
row costs one row, not the file. The run still exits non-zero, because a
scheduled load that quietly drops rows is how data goes missing unnoticed.

Rows carrying a byte the database cannot store — a NUL, an unpaired surrogate —
are screened out before the write is attempted and recorded under stage
`screen`, naming the offending column. The screen only refuses what Postgres
certainly refuses; anything it misses is still caught by the write, because a
row wrongly let through costs a round-trip while a row wrongly rejected would
leave the entity graph with nothing to say so.

```powershell
# What could not be processed, and why
docker compose run --rm pipeline process errors

# Release a batch left 'running' by a process that died. Only ever marks it
# failed; the rows it already wrote stay exactly where they are.
docker compose run --rm pipeline process abandon <batch-id>
```

Useful flags: `--record-id-column <col>` for a natural key instead of the row
number, `--reliability 0.8` to record how much this source is trusted,
`--allow-reingest` to deliberately process a file already seen, `--read-ahead N`
for how many rows are pulled off disk per read (buffering only — records are
still processed one at a time), `--no-golden` to skip rebuilding each record's
entity as it lands, `--fail-fast` to stop at the first record that throws, and
`--async-commit` to defer the commit fsync (~1.7x faster; a crash can lose
recently committed records, which leaves the batch un-completed and therefore
ignored downstream — recovery is re-running the file).

Measured on 20,000 records, and against the batch path on an identical file, in
[ADR 0012](../decisions/0012-record-at-a-time-processing.md).

## Ingesting a file (batch path)

The stage-by-stage path is still how *reprocessing* works, and `ingest` is its
entry point:

```powershell
docker compose run --rm ingestion ingest /data/inbox/fixtures/messy_people.csv `
  --entity-type person --source-name vendor_x
```

Drop your own files into `data/inbox/` (bind-mounted to `/data/inbox` in the
container). Useful flags: `--record-id-column <col>` to use a natural key
instead of the row number, `--allow-reingest` to deliberately re-ingest a file
already seen, `--reliability 0.8` to record how much this source is trusted,
`--batch-size N` to change how many rows are read and committed at a time
(default 5,000).

### Large files

Rows are **streamed and committed a batch at a time**, so peak memory is a batch
rather than the file:

| Rows | Peak memory, whole file | Peak memory, batched |
| --- | --- | --- |
| 20,000 | 15.4 MB | **7.8 MB** |
| 100,000 | 77.0 MB | **7.8 MB** |
| 250,000 | 193.5 MB | **7.8 MB** |

If a batch fails part-way, the rows already written **stay** — they are what the
source said — and the batch is marked `failed`. Every downstream stage requires
`completed`, so partial rows are inert rather than dangerous.

Excel is batched but still read whole through this path: the format is a zip
archive whose rows cannot be reached without decompressing the sheet. (
`tools/fixtures/sample_file.py` does stream it, with openpyxl in read-only mode, which
is how a 386,327-row sheet is sampled at ~280 MB.)

**A workbook with more than one sheet of data must say which to load.** Sheets
are separate layouts — `LeadsNemo_Test1.xlsx` carries two with different column
orders — and taking the first silently dropped 19,926 records with nothing
recording the gap. Empty sheets are ignored, since a couple of blank ones
nobody deleted is not ambiguity.

### Duplicate files

A file is identified by the **SHA-256 of its contents**, not its name, so a
renamed copy is caught and a same-named file with new contents is not. Three
outcomes:

- already ingested for this source → **blocked** (exit 3, `--allow-reingest` overrides)
- currently being ingested for this source → **blocked**, and `--allow-reingest`
  deliberately does *not* override it — re-ingesting later is a choice, racing
  yourself never is
- identical content under a *different* source name → **warns and continues**,
  since two vendors genuinely can ship the same file, though it is usually a
  typo in `--source-name`

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

## Working with real vendor files

Real exports are large and carry personal data, so `testfiles/` is gitignored
and never loaded whole. Cut a bounded sample per layout instead — every sheet
of a workbook becomes its own file, because sheets are separate layouts:

```powershell
docker compose run --rm pipeline python /tools/fixtures/sample_file.py /tf --all --rows 1000

# Which columns would need a human before loading anything
docker compose run --rm pipeline python /tools/measure/mapping_coverage.py
```

### Testing the cleaning pipeline without real data

Vendor files prove the pipeline survives reality; they are bad at proving it is
*correct*, because nothing in them states what the right answer was. For that
there is a generated file where every defect is planted deliberately:

```powershell
python tools/fixtures/generate_edge_cases.py data/inbox

docker compose run --rm pipeline process run /data/inbox/edge_cases.csv `
  --entity-type company --source-name edge_cases --source-type csv `
  --record-id-column record_id --describes organisation

docker compose exec -T postgres psql -U pcdf_dev -d pcdf `
  -v batch="'<batch_id>'" < tools/sql/verify_edge_cases.sql
```

500 rows, 57 planted cases. Each row's `record_id` names what it proves —
`EDGE-011` is the Excel serial date, `DUP-003` the email match, `NEAR-002` the
pair that must *not* merge — so a result can be checked against an intention
rather than eyeballed. The column headers are a vendor's (`E-Mail`, `mobile_no`,
`house_address`, `# Employees`) on purpose: testing with our own vocabulary
would skip the mapping stage, which is the stage most likely to be wrong about
a real file.

It has already earned its keep. Its first run found that a company file's
`mobile_no` column mapped to nothing — 496 phone numbers captured but
uncanonical, and, worse, three phone rules that never ran at all, so a number
too short to dial passed in silence. Unmapped values are not validated, which
makes a mapping gap look exactly like clean data.

To prove a fix rather than assert it, load the same file again under a second
source name and diff the two runs — the input is byte-identical, so every
difference is the code:

```powershell
docker compose exec -T postgres psql -U pcdf_dev -d pcdf `
  -v before="'<batch>'" -v after="'<batch>'" < tools/sql/compare_runs.sql
```

`sample_file.py` streams Excel with openpyxl in read-only mode, which is how a
386,327 x 55 sheet is read at ~280 MB instead of materializing the workbook.
