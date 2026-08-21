# Processing files

How a file actually gets from your disk into a golden record, and the
guarantees at each step.

Part of the [People & Company Data Foundation](../../README.md).

## The path that matters: record-at-a-time

Whether triggered by dropping a file into a [watched feed](../../data/inbox/watch/README.md)
or run directly, this is the path that loads a file:

```powershell
docker compose run --rm pipeline process run /data/inbox/fixtures/messy_people.csv `
  --entity-type person --source-name vendor_x --reliability 0.8 --describes organisation
```

Each record is normalized, validated, quarantined-or-not, and resolved to an
entity — with its golden values rebuilt — **before the next record starts**.
Nothing here waits on a message queue or a separate consumer process; it's
one function call per record, straight through.

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

**A record that cannot be processed fails alone.** It's rolled back, written
to `record_error` with its full payload and the error, and the run
continues — one bad row costs one row, not the file. The run still exits
non-zero, because a scheduled load that quietly drops rows is how data goes
missing unnoticed.

```powershell
docker compose run --rm pipeline process errors           # what failed, and why
docker compose run --rm pipeline process abandon <batch-id>  # release a batch left "running" by a dead process
```

Useful flags: `--record-id-column <col>` for a natural key instead of the row
number, `--allow-reingest` to deliberately reprocess a file already seen,
`--read-ahead N` for read buffering (records still process one at a time),
`--no-golden` to skip rebuilding entities as they land, `--fail-fast` to stop
at the first record that throws, `--async-commit` to defer the commit fsync
(faster; a crash can lose recently committed records — recovery is
re-running the file).

## The other path: stage-by-stage, for reprocessing

The individual stage CLIs exist for **redoing** work already done — a rule
changed, a schema field got added, a batch needs re-validating — not for
loading a new file. None of them trigger the next one automatically; you
chain them by hand, in order:

```powershell
docker compose run --rm ingestion ingest /data/inbox/fixtures/messy_people.csv `
  --entity-type person --source-name vendor_x

docker compose run --rm mapping       map-schema --batch-id <id>
docker compose run --rm normalization normalize  --batch-id <id>
docker compose run --rm validation    validate   --batch-id <id>
docker compose run --rm resolution    resolve run --batch-id <id>
docker compose run --rm golden        golden build --batch-id <id>
```

`ingest` only writes raw rows — no mapping, normalizing, validating, or
resolving. Each later stage reads what the previous one wrote from Postgres;
the event that reaches it carries a batch id and nothing else.

Once the rows are committed, `ingest` publishes `pcdf.batch.ingested` and the
five stage consumers carry the batch the rest of the way on their own. To do
the stages by hand instead — reprocessing after a rule change, say — run them
with `docker compose run --rm <service> …` as below; the CLI and the consumer
call the same function.

### Large files

Rows are **streamed and committed a batch at a time** (default 5,000, change
with `--batch-size N`), so peak memory is a batch, not the file:

| Rows | Peak memory, whole file | Peak memory, batched |
| --- | --- | --- |
| 20,000 | 15.4 MB | **7.8 MB** |
| 100,000 | 77.0 MB | **7.8 MB** |
| 250,000 | 193.5 MB | **7.8 MB** |

If a batch fails part-way, rows already written **stay** — they're what the
source said — and the batch is marked `failed`. Every downstream stage
requires `completed`, so partial rows are inert rather than dangerous.

**Excel is the one exception.** The format is a zip archive whose rows can't
be reached without decompressing the whole sheet, so the read step itself
isn't streamed — batching still applies to everything after that read.

**A workbook with more than one sheet of data must say which to load.**
Taking the first sheet silently drops every row on the others. Empty sheets
are ignored — a couple of blank ones nobody deleted isn't ambiguity.

### Duplicate files

A file is identified by the **SHA-256 of its contents**, not its name:

- already ingested for this source → **blocked** (exit 3, `--allow-reingest` overrides)
- currently being ingested for this source → **blocked**, and `--allow-reingest`
  deliberately doesn't override — re-ingesting later is a choice, racing
  yourself never is
- identical content under a *different* source name → **warns and
  continues** (usually a typo in `--source-name`, but two vendors genuinely
  can ship the same file)

Exit codes: `0` ok · `2` bad input · `3` already ingested · `5` finished, some
rows failed (not a crash — check `process errors`).

> **Run these from PowerShell, not Git Bash** — Git Bash rewrites container
> paths like `/data/inbox/x.csv` into Windows paths.

### What ingestion guarantees

Source values are stored **exactly as written** — no type inference. Leading
zeros (`00501`), long IDs, `9.99E10`, unicode, original date formats all
survive intact. Whitespace and casing are deliberately left dirty; cleaning
is normalization's job, done later, from the original.

## Working with real vendor files

Real exports are large and carry personal data — `testfiles/` is gitignored
and never loaded whole. Cut a bounded sample per layout instead (every sheet
of a workbook is its own layout, sample each separately):

```powershell
docker compose run --rm pipeline python /tools/fixtures/sample_file.py /tf --all --rows 1000

# Which columns would need a human before loading anything
docker compose run --rm pipeline python /tools/measure/mapping_coverage.py
```

To prove a fix rather than assert it, load the same file twice under two
source names and diff the batches — same bytes in, so every difference is
the code:

```powershell
docker compose exec -T postgres psql -U pcdf_dev -d pcdf `
  -v before="'<batch>'" -v after="'<batch>'" < tools/sql/compare_runs.sql
```
