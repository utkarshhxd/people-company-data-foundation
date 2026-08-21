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

Want both in one page — pipeline progress and the four review queues,
tabbed — open `http://localhost:8000/admin/page` instead. It's the same
data and the same decision endpoints; this page and `/review/page` still
work on their own too.

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
