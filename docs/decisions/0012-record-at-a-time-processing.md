# ADR 0012: Record-at-a-time processing

## Status

Accepted.

## Context

The pipeline processed a file stage by stage: ingest every row, then map, then
normalize every row, then validate every row, and so on. Each stage read its
input from Postgres and wrote its output back, in chunks.

The decision was taken to make the **record** the unit of work instead: one
record carried the whole way through — normalized, validated, quarantined or
resolved, and its entity's golden values rebuilt — before the next record
starts. The motivation is that records become individually deliverable to
another system as they finish, and that a record's fate stops depending on the
19,999 rows it happened to share a file with.

A measured objection was raised first. Per-record commits on a single table ran
19x slower than batched ones, and that number is real. This ADR records what was
done about it, because 19x would not have been acceptable and did not turn out
to be the actual cost.

## Decision

`services/pipeline` runs one record at a time, end to end. It **implements no
stage logic of its own**: every decision still comes from the module that owns
it — `observations_for_record`, `validate_record`, `route`, `keys_for`,
`apply_decision`, `build_entity`. This service chooses the order and the
transaction boundary, and nothing else.

That constraint is what makes two paths tolerable. The batch services remain,
because reprocessing already-stored records is a genuinely different job from
loading a file — re-validating everything after a `RULESET_VERSION` bump reads
from Postgres and never touches the source. Both paths call the same functions,
so they cannot drift apart in what a record *means*.

### Shape of one record's turn

```
1. compute      normalize + validate entirely in memory, no database at all
2. one read     which entities do this record's identity keys already reach?
3. one write    everything the record produces, including the entity's rebuilt
                golden values — pipelined, one transaction, one commit
```

Steps 1 and 2 exist to make step 3 a single flush. Nothing in the write waits on
a value coming back from the server, because the ids are chosen in the process
rather than by the column defaults.

Golden building sits **inside** that transaction, which was not the first
attempt. It began as a fourth step after the commit, and profiling the
20,000-record run showed the pipeline was fsync-bound — two commits per record
meant 40,000 fsyncs. Folding it in halves that, and it works because a
transaction sees its own uncommitted writes: `build_entity` reads the
observations the same transaction just wrote.

The correctness argument is the better one, though. With golden inside, there is
no window in which a record exists but its entity's values have not caught up.
When `process()` returns there is nothing outstanding about that record, which
is exactly what makes it safe to hand straight to another system.

## Making it affordable

The naive implementation of "one record at a time" is 11 round-trips and an
fsync per record. Four changes, each measured separately:

| variant | rec/s | vs batched |
| --- | --- | --- |
| batched, 1000/txn (the old architecture) | 1,236 | 1.00x |
| per-record, naive | 187 | 6.61x slower |
| + client-side UUIDv7, one `executemany` per record | 301 | 4.11x |
| + libpq pipeline mode, synced per record | 335 | 3.69x |
| + `synchronous_commit = off` | 578 | **2.14x** |

5,000 records x 10 observations each, same table, same machine. The stack
recovers **3.09x** over the naive version.

**Loaded once, never per record.** The column mapping, canonical field specs,
source and batch identity. Per-record work must only touch what varies per
record; anything else gets multiplied by the record count for nothing.

**Client-side UUIDv7** (`common/ids.py`). A record's observations reference the
record, and its validation results reference the observations. With server-side
ids each level waits for the previous level's `RETURNING`. Choosing the ids in
the process removes the dependency entirely. The value is a real RFC 9562
UUIDv7, the same thing `uuidv7()` produces, so ids from either source sort
together and cannot be told apart afterwards.

**Pipeline mode, synced once per record.** The statements for one record go out
without waiting for each result; the commit syncs. The record is still exactly
one transaction — it lands whole or not at all. Syncing *per record* rather than
across records is what keeps that true.

**`synchronous_commit = off`**, behind `--async-commit` and off by default. This
is the one with a real trade-off, and it is smaller than it looks. Atomicity and
isolation are untouched; only the fsync is deferred, so a crash can lose
recently committed transactions but never corrupt or half-apply one. Postgres'
WAL is a single ordered stream, which means a crash can only lose a *suffix* —
and the batch is marked `completed` after the last record, so a lost suffix
always leaves the batch un-completed and therefore inert to every downstream
stage. Recovery is re-running the file. That property is why the flag is
offerable at all.

**Read-ahead is not granularity.** `iter_rows(path, read_ahead)` pulls rows off
disk in chunks and hands them over one at a time. How much the disk gives up per
syscall is not a fact about the data; how much is processed at once decides what
fails together. Conflating the two is what made "batch processing" sound like it
meant something about correctness.

### The end-to-end cost is not the micro-benchmark cost

Running the same 500-row file down both paths, the per-record path took **1.05x**
the batch path's time. The micro-benchmark measures the part per-record
processing makes worse; the full pipeline is dominated by validation compute,
which is identical either way — and the batch path *already* commits per record
in resolution and per entity in golden, for the same ordering reasons.

## At 20,000 records

```
read       20,000
processed  20,000
failed          0
280.2s wall, 71 records/s
```

| table | rows |
| --- | --- |
| `raw_record` | 20,000 |
| `attribute_observation` | 200,000 |
| `record_validation` | 20,000 |
| `validation_result` | 483,429 |
| `record_entity_link` | 20,000 |
| `record_error` | 0 |
| `golden_attribute` (current) | 173,143 |

Counts reconcile across every table. The rules fired exactly on the planted
defects — `phone.filler` 645 times against a defect planted every 31st row
(20000/31), `address.spacing` 377 times against every 53rd (20000/53) — which
are the same counts the batch path produced on the same construction in
increment 11.

Before folding golden into the record's transaction, the same run sustained
roughly 13-19 records/s and was visibly degrading. Sampling `pg_stat_activity`
showed `COMMIT` as the most common active statement, which is what pointed at
the second commit rather than at a query plan.

## Proving the two paths agree

Reuse is an intention, not a proof. The per-record path reconstructs in memory
several things the batch path reads back out of Postgres, and a reconstruction
can be subtly wrong.

`data/inbox/_parity.py` runs one generated 500-row file down both paths, each
against its own clean starting state, and compares every derived artifact per
row: observation count, canonical count, null tokens, validation status, error
and warning counts, validated and unvalidated attribute counts, failed rules,
judgement count, identity keys built, and match status.

**Identical across all 500 rows**, including the 21 records validation ruled
invalid and quarantined and the 28 it warned on.

Two ordering details were required to get there, both worth naming because
neither would have shown up in a smaller test:

- **`_confirmed_values` sorts by `(source_column, value_index)`** to match
  resolution's own query. Identity keys are built from these values in order, so
  a different order could build a different key from the same record — and then
  the two paths would produce different entities from the same file.
- **Observation dicts carry exactly the keys `iter_records` yields**, so
  `validate_record` cannot tell whether its input came from memory or a query.

A first attempt at this comparison ran both paths against a shared state and
reported 216 divergences. That was the test being wrong, not the code: the
second path correctly *linked* to the entities the first had just built. Which
is the cross-dataset linking requirement working — and exactly why it cannot be
used as evidence that the two paths agree.

## Failure isolation, which is the point

A record that throws is rolled back, written to `record_error` with its payload
and its error, and the run continues. One unprocessable row costs one row.

`record_error` is deliberately **not** quarantine. Quarantine holds records the
pipeline understood and judged unusable, with reason codes from the ruleset.
`record_error` holds records it could not process at all. One is a verdict about
the data; the other is a failure of ours, and conflating them would let our own
defects masquerade as the vendor's.

Verified by feeding it a file with a NUL byte in two of ten rows — something
real vendor exports do contain:

```
read       10
processed   8
failed      2      <- rows 4 and 8, the poisoned ones
```

The run finished and exited non-zero. A scheduled load that quietly drops rows
is how data goes missing unnoticed, so a partially successful run is still a
failed run as far as the exit code is concerned.

That test also found a hole in the net: `record_error.raw_payload` was `jsonb`,
and a payload Postgres cannot store is exactly the payload that ends up here.
The insert into the error table failed for the same reason as the record, and
the run died anyway. Migration 0010 splits it: `raw_payload` is sanitized so it
can always be stored and read, `raw_payload_bytes` holds the payload exactly as
it arrived, and `payload_sanitized` says whether the two differ. Nothing written
to this table may itself be rejectable.

## Three defects found along the way

**Sixteen foreign keys had no index** (migrations 0008 and 0011). Postgres
indexes the side a foreign key points *to*, never the side it points *from*, so
every delete of a referenced row proved no child referenced it by sequentially
scanning the child table.

Found twice, both times by something taking absurdly long:

- Removing 50,000 observations took over eleven minutes, because
  `validation_result.observation_id` was unindexed against a table holding
  ~500,000 rows. After migration 0008: 250,000 observations removed in under a
  second.
- Removing one run's golden values was still going after twenty minutes, because
  `golden_attribute.superseded_by` references `golden_attribute` itself. After
  migration 0011: seconds.

Ten were indexed and five deliberately were not. `source_id` on
`attribute_observation`, `record_validation`, `quarantine_item`,
`record_entity_link` and `record_error` has almost no selectivity — a handful of
sources against millions of observations — so an index would rarely be chosen
for a read while costing a write on all ten observations of every record. The
only thing it would buy is a faster `DELETE FROM source`, and sources are not
deleted: a source is the provenance of everything beneath it. "Index every
foreign key" is a rule of thumb, not a reason.

**The migration runner silently truncated a file containing a NUL byte.** A NUL
terminates the C string handed to libpq, so Postgres stopped reading there,
applied only what came before it, and the runner recorded the migration as
applied. The result is a schema that does not match its own migration history.
It happened to migration 0010 — whose comment described NUL handling and
contained one — which is how it was noticed. `_reject_unexecutable` now refuses
such a file, for every migration on every run, applied or not.

**Golden record building is not safe against concurrent builders.** Two
processes building the same entity both close the current value and both insert,
violating the single-current-value index. Hit by running the batch stages
manually while the Kafka consumer chain was processing the same batch. Not
introduced here and not fixed here — the per-record path builds inline in one
process — but it is real, and it is recorded in the follow-ups below.

## Consequences

- **`pcdf.record.processed` is emitted per record**, keyed by entity id where
  there is one so an entity's records land on one partition in order. It carries
  references and routing status only — never values. A consumer reads them from
  Postgres or the API, so there is one place where what we know about a record
  lives, and an event cannot go stale against it.
- **Ordering is now load-bearing.** Records go through in file order because
  record 900 must be able to match the entity record 12 created. This was
  already true of resolution; it now constrains the whole pipeline, and it is
  what stands between this design and processing records in parallel.
- **Parallelism is the next lever and is not free.** Normalization and
  validation are independent per record and would parallelize cleanly.
  Resolution would not: it would need partitioning by blocking key so records
  that could match each other reach the same worker. Not attempted.
- **Two entry points now exist for the same stages.** Justified because loading
  a file and reprocessing stored records are different jobs, and mitigated
  because both call the same functions. It is still two things to keep in mind.
- **`--async-commit` is off by default** and should stay off for anything whose
  source file will not still be around to re-run.
- **Excel remains resident.** Unchanged and unchangeable here: the format is a
  zip archive whose rows cannot be read without decompressing the sheet.

## Follow-ups

- Make golden building safe under concurrency, or make it explicitly
  single-writer per entity.
- A `reprocess` command for `record_error` rows, now that the payload is kept
  byte-exact.
- Decide whether the batch stage CLIs should refuse to run while their consumer
  is live, which is what caused the golden collision above.
