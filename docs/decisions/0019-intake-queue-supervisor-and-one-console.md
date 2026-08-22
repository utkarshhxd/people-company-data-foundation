# ADR 0019: An intake queue, an automatic stop, and one console instead of three

## Status

Accepted.

## Context

Four things were asked for together, and they turn out to be the same problem
seen from four sides: the system could do more than it could *show*, and the
parts that were invisible were the parts that mattered during an incident.

1. Dropping several files at once should be a queue you can see, not ten
   concurrent loads you cannot.
2. When a service goes away, the system should stop taking new work in rather
   than piling it up until somebody notices.
3. What happened — and why something stopped — should be readable in the
   browser, not only behind `docker compose logs <the right container>`.
4. The console should be one product, not three pages that had drifted apart.

---

## 1. The upload becomes a row before it becomes work

**Decision.** A dropped file is written to disk, and a row is written to
`ingest_queue` describing what was asked for. One worker claims one row at a
time and runs the same `ingestion.pipeline.ingest()` the CLI runs.

**What it replaces.** The old path started ingesting inside a background thread
the moment the bytes finished arriving, tracked in a dictionary in one process.
That is fine for one file and wrong for ten:

* ten uploads meant ten concurrent ingests, ten connections, ten batches
  interleaving their writes against the same tables;
* a restart in the middle lost every record of what had been asked for — the
  bytes were on disk with nothing left saying what they were meant to be loaded
  as;
* the queue was one browser's private view of its own uploads, so two people
  loading files could not see each other's.

**Why one worker and not a pool.** The serialisation is the feature. A pool
would put the interleaving back, and the throughput this is measured on is
records per batch, not batches per second.

**Why the arguments are per drop, not per file.** Entity type, source name and
reliability are properties of the *feed*, not of the file — the same reasoning
`record_pipeline.watch` moves them onto a feed directory for. Re-typing them per
file is how one vendor's data ends up loaded under two source names at two
reliabilities.

**Abandonment.** A worker that dies mid-load would otherwise leave a row saying
`running` forever, with its file silently never loaded — exactly the quiet loss
the table exists to prevent. So the worker touches `heartbeat_at` while it is
inside `ingest()`, and a row that stops being touched is queued again.

**`held` is not `failed`.** When ingestion is stopped, waiting files are marked
`held`, with the reason. A held file needs nothing done to it. A failed one
invites somebody to re-upload a file that was never wrong.

> A bug found while verifying this, worth recording because the fix is the
> interesting part: holding was originally done only on the *transition* into
> paused, to avoid an update per poll against an idle database. That misses the
> case that matters most — a file dropped *during* a pause arrives after the
> transition, and sat as `queued` with nothing coming for it and nothing saying
> why. Waiting items are now reconciled on every sweep (a partial-indexed
> update that normally matches no rows), and `enqueue` writes `held` directly
> when ingestion is already stopped, so there is no window at all.

## 2. Ingestion stops itself when the things that would process the work are gone

**Decision.** The console runs a supervisor. Every 30 seconds it reads the
heartbeat ages of the five consumers and the watcher, and probes the broker.
If any of them has not reported for three minutes, or the broker is
unreachable, it pauses **ingestion** — with the reason, and with the evidence
attached to the event.

**Why ingestion and nothing else.** It is the one stage that decides whether
*new* work enters the system. Everything downstream of it queues by design, so
one switch is enough. Pausing a stage whose own worker is already dead would
add nothing.

**Why this is worth automating.** The failure is quiet, which is what makes it
dangerous. Resolution's container dies at two in the morning: nothing breaks.
The watcher keeps loading files, ingestion keeps committing batches, Kafka keeps
accepting announcements, and all of it piles up on a topic nobody is reading. By
nine there is a day of backlog, a day of files in `_done/` that are not actually
done, and no moment anybody can point at as the start.

**Liveness is the age of a heartbeat, not the existence of a container.** A
consumer wedged on a broker that accepts connections and never delivers keeps
its container "up" and does nothing. `restart: unless-stopped` cannot see that.

### The part that needed a decision: this pause clears itself

ADR 0018 and `common.control` are explicit that nothing resumes on its own, and
for the two callers they were written for that is right. A person's pause is a
judgement that something looks wrong. The validation breaker's pause is a
judgement that what is coming back does not look like data. Both need somebody
to decide the reason is gone; an automatic clear would mean a pause only ever
observed by whoever happened to be watching.

This one is not a judgement. It is a **reflex to an observable fact** — a
process is not beating — and the same observation says when it is over. So:

* a supervisor pause is undone by the supervisor, and only after the services
  have been back for three consecutive checks (a service flapping every other
  check never accumulates enough);
* it will **only ever** undo a pause it set itself. A stage stopped by a person,
  or by the breaker, is never touched — not the state, and not the reason;
* `SUPERVISOR_AUTO_RESUME=false` requires a human either way;
* every transition, in both directions, is written to `pipeline_control_event`
  with its evidence. An automatic action nobody can reconstruct afterwards is
  worse than no automatic action.

`decide()` is a pure function separated from the writing for exactly this
reason: every branch is a rule about when an automatic actor may overrule a
person, and those should be readable and testable without a database.

## 3. What happened, in the browser

**Decision.** Two things, kept separate because they answer different questions.

**An activity feed** (`/control/activity`) that unions what the database
already records: batches loaded and failed, the intake queue, stops and starts
with their reasons, rows that threw, and what services said. Nothing is written
by that module — every entry is a projection of something already durable.

**A bounded tail of the service logs** (`service_log`, `common.dblog`). Every
service already prints what went wrong; the problem was only where it landed —
one container's stdout, readable by whoever is on the host and knows which of
eleven containers to look in.

Three rules make a log handler safe to attach to the root logger of a process
that must not stop:

* **It never blocks the caller.** `emit` puts a row on a bounded queue and
  returns; a background thread inserts. If the queue fills, rows are dropped
  and counted — a service that stalls on its own logging is worse than one
  whose log has a gap, and the gap says it is a gap, because the drop count is
  written as a row of its own.
* **It never recurses.** The writer thread's own logging is not captured.
  Otherwise one failed insert logs a warning, which becomes a row, which fails
  to insert.
* **It never grows without bound.** WARNING and above only, trimmed to a fixed
  number of rows. Postgres is not a log store.

Container stdout is unaffected and remains the complete record.

**What was rejected.** Reading container logs directly. It would need the Docker
socket mounted into the console, which is root on the host — an unacceptable
trade for a convenience, in a service that is deliberately allowed to run
unauthenticated on loopback.

## 4. One console

**Decision.** The stylesheet, the shared helpers and the four review-queue
renderers move to `assets.py`, and all three pages compose from them.

There were three copies, and they had drifted: `ago()` returned "unknown" on two
pages and an em-dash on the third, `.empty` was green on two and grey on the
third, and — the one that mattered — the two copies of the duplicate-comparison
renderer had drifted apart in what they call the sides of the comparison, on a
screen whose output is a permanent merge.

The page bodies are now **raw strings**. These files are Python strings
containing JavaScript, and a `\n` written for the browser is consumed by Python
first: the browser receives a real line break inside a string literal, the
script stops parsing, and the panel renders blank with no error anybody sees.
That had already happened twice. `test_pages.py` still checks the symptom, and
now also checks that nobody has grown a second stylesheet or shadowed a shared
helper.

Two numbers labelled "Failed" on the same screen — `batch.rows_skipped` and
`count(*) FROM record_error` — are now named separately ("Skipped" and "rows
that threw"). They legitimately disagree, and nobody could tell which was which.

The five-second refresh no longer replaces a review queue while somebody is
typing into it. A considered note should not be a race against a timer.

## Consequences

* Ten files is a queue, visible to everyone, that survives a console restart.
* A service disappearing stops intake within about thirty seconds and says so,
  in a banner, on every tab — and starts again on its own when it comes back,
  without ever undoing a decision a person made.
* "Why did that stop" is answerable from the browser.
* Three new gauges (`pcdf_ingest_queue_items`,
  `pcdf_oldest_waiting_file_age_seconds`, `pcdf_service_log_entries`) and three
  new alerts, keeping the house style of alerting on **age rather than depth**.
* Two new tables (`ingest_queue`, `service_log`) and two new background threads
  in the console. Both threads are single-instance by nature, which is why they
  are threads in the one long-running HTTP process rather than containers; the
  queue worker additionally takes a Postgres advisory lock to claim, so a second
  console could never take the same file twice.
