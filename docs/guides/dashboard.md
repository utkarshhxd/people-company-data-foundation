# Pipeline dashboard

Seeing how far a batch's records got, without a terminal.

Part of the [People & Company Data Foundation](../../README.md).

Records move [record-at-a-time](../decisions/0012-record-at-a-time-processing.md):
one record is normalized, validated, quarantined-or-not, resolved and
golden-built inside a single transaction, so a record that has finished
almost always lands in one of two places — fully through, or stuck in a
queue (schema mapping needs review, quarantined, a possible duplicate
waiting on a person). The dashboard shows that split per batch, and how far
an in-flight batch has gotten so far.

## In a browser

Same service as the [review queues](review-console.md) — no separate
process, no new port:

```bash
docker compose up -d
open http://localhost:8000/dashboard/page
```

Want everything in one page — loading files, pipeline progress, the four
review queues, operations and the activity log, tabbed — open
`http://localhost:8000/admin/page` instead. It's the same data and the same
decision endpoints; this page and `/review/page` still work on their own too.
Number keys 1&ndash;9 switch tabs, `[` and `]` step through them, `r` reloads
the one you are on.

This page only reads. Nothing here can change a record's state; use the
[review console](review-console.md) or the stage CLIs for that.

Gated the same way as the review console: unset `PCDF_API_KEYS` and
`PCDF_ALLOW_UNAUTHENTICATED` and it serves only to loopback; set one to open
it up. See `.env.example` and
`services/review_console/src/review_console/auth.py`.

## What it shows

**All batches, combined** — a funnel across everything ever loaded: how many
records were ingested, normalized, validated (split valid / warning /
invalid), how many are sitting open in quarantine or as a possible-duplicate
candidate, how many resolved to an entity, and how many have a golden record
built.

**Recent batches** — one row per file load, with `rows_read` updating live
while the batch is still `running`: that column is written once per record as
the run commits it, so a 10-million-row load in progress is watched, not
guessed at.

**One batch, expanded** — click a batch for the same funnel scoped to just
its records, plus whatever failed outright (`record_error`, distinct from
quarantine — see [operations](operations.md)) and whatever schema mapping
question that batch's column layout first raised.

## Intake

The second tab on `/admin/page`. Drop as many files as you like at once: they
are written to disk, queued, and loaded **one at a time in the order they
arrived**, so a ten-file drop is a queue rather than ten loads competing for the
same tables.

The settings apply to every file in the drop, on purpose — entity type, source
name and reliability are properties of the *feed*, not of the file, and
re-typing them per file is how one vendor's data ends up loaded under two source
names at two reliabilities. (One exception: a sheet name only applies to a
single-file drop, since it cannot mean anything sensible across several
workbooks.)

The queue below shows what is waiting, what is loading, and what became of
everything else. A waiting file can be cancelled; a finished one can be queued
again without re-uploading it, which is most of what this is for — a load that
failed because a source name was wrong should be one click, not another upload.

Three things worth knowing:

* The queue is a **table**, not a browser session. Everyone sees the same one,
  and it survives a console restart. A file whose worker died is picked up
  again rather than sitting there with nothing loading it.
* **`held` is not `failed`.** When ingestion is stopped, waiting files are held,
  with the reason attached. They start moving again on their own when it does.
* Ingestion refuses the **same bytes twice** under the same source, whatever the
  file is called — so a re-drop of a file already loaded fails with a message
  saying which batch it was, rather than doubling the data. `--allow-reingest`,
  or the checkbox under Advanced, overrides that deliberately.

## Operations

The eighth tab on `/admin/page`, and the only one that changes anything about
the pipeline rather than about a record.

**Stop and start.** Every stage, with its state and, if it is stopped, why and
who stopped it. Pausing asks for a reason, because whoever finds the pipeline
stopped is rarely the person who stopped it. Nothing is discarded by a pause:
whatever is mid-record finishes and is recorded, queued work waits in its Kafka
topic, and files stay in their feed directories — what stops is anything new
starting. Validation also stops itself when almost everything coming through it
is invalid; see [the runbook](../runbook.md#validation-stopped-itself).

**Automatic stop.** Whether anything is watching for a service going away, and
what it last saw. If a consumer, the watcher or the broker stops reporting for
three minutes, ingestion is stopped automatically: new files are held rather
than loaded, and nothing already in the system is discarded — records in flight
finish and commit, and backlogs wait in their topics. It starts again on its own
once everything has been back for three consecutive checks.

This is the **only** stop that clears itself. A stage stopped by a person, or by
validation stopping itself, is never touched — those are judgements about the
data and need somebody to decide the reason is gone; this is a reflex to a
process not being there, and the same observation says when it is over. Set
`SUPERVISOR_AUTO_RESUME=false` to require a person either way, or
`SUPERVISOR_ENABLED=false` to switch it off entirely. Every automatic stop and
start is in the history with the evidence that caused it.

**Services.** How long ago each long-running process last completed a loop. A
container being up is not the same as its loop turning: a consumer blocked on a
broker that accepts connections and never delivers stays "up" forever. Green
under 15 minutes, amber to an hour, red past it.

**Queued work.** Consumer lag per group: messages published to a topic that the
consumer has not committed yet. Non-zero during a load is normal; non-zero and
not falling means a stage is stopped or cannot keep up. If the broker cannot be
reached this says so rather than showing zero — "nothing is waiting" and "we
cannot tell what is waiting" are opposite states.

**Alerts.** What Prometheus is currently firing, worst first, read through
Alertmanager. Same distinction: an unreachable Alertmanager is an error here,
not an empty list.

**Data integrity.** The twelve reconciliation checks in `tools/sql/verify.sql`,
run on demand. Each returns rows only when something is wrong, so all-empty is
the pass. They read every table, which is why they run when asked rather than on
every render.

Everything here is also available as JSON — `GET /control/health`,
`GET /control/supervisor`, `GET /alerts`, `POST /control/integrity`,
`POST /control/supervisor/check`, and `POST /control/{stage}/pause` and
`/resume` — and as the same functions the `validation-control` CLI calls, so a
stage stopped from a browser and one stopped from a terminal are one code path.

## Activity

The ninth tab: what happened, newest first, filterable by severity and by kind.
It reads four things the database already records — files loaded and failed,
the intake queue, every stop and start with its reason, and rows that threw
part-way — plus a bounded tail of what the services themselves logged at
WARNING and above.

That last part is a convenience, not a replacement: each container's stdout is
still the complete record, and a service that cannot write here carries on
unaffected. What is kept in the database is capped, and dropped rather than
queued if Postgres cannot keep up — with the number dropped written as a row of
its own, so a gap in the log says it is a gap instead of reading as a quiet
period. Turn it off per service with `LOG_TO_DATABASE=false`.

Also JSON: `GET /control/activity` and `GET /control/logs`.

## Reading a funnel that doesn't taper evenly

Because a record clears every stage in one commit, `normalized` and
`validated` will almost always equal `ingested` — that is not a sign
nothing happened, it is the record-at-a-time design working as intended.
Where the funnel actually narrows is at **validation** (invalid records stop
short of resolution until quarantine releases them) and at the two review
queues (**mapping needs review**, **possible duplicates open**). Those
numbers are where records are actually waiting on a person; everything else
reads close to 100% by construction.

## API

The page is a shell over three JSON endpoints, same key as `/review/*`:

```
GET /dashboard/summary            -- funnel across every batch
GET /dashboard/batches            -- recent batches, most recent first
GET /dashboard/batches/{batch_id} -- one batch's counters + funnel
```
