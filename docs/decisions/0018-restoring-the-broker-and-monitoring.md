# ADR 0018: Kafka, Prometheus and Grafana come back

## Status

Accepted.

## Context

A large uncommitted change removed Kafka, Prometheus, Grafana, Alertmanager,
the `services/api` metrics endpoint and `tools/measure/` — 45 file deletions,
no commit, no ADR. The prose written alongside it argued the broker bought
nothing: "A record moving from one stage to the next is a function call or a
row already sitting in Postgres — never an event on a queue."

That argument is right about one thing and wrong about the decision it was
used to justify.

It is right that **a record must not travel on a broker.** ADR 0012 put
normalization, validation, quarantine routing, resolution and golden-record
building inside one transaction per record, precisely so that "a record and
its consequences land together or not at all." Splitting those across topics
would reintroduce the window that design closed. Ordering makes it worse:
resolution requires file order, because record 900 has to be able to match the
entity record 12 created, and a record carries several identity keys at once
(`external_id`, `linkedin`, `email`, `name_company`, `name_city`, `phone` —
`resolution/keys.py`). Everything that might reach one entity has to serialize,
and that set is a transitive closure over a graph of key types, not a value a
partitioner can compute. The per-key `pg_advisory_xact_lock` in
`resolution/repository.py` expresses it; a partition key cannot.

It was wrong to conclude that the broker therefore had no place. Two things
were overlooked:

- **ADR 0001 records Apache Kafka and Grafana + Prometheus among the project's
  hard constraints.** Nothing superseded that.
- **ADR 0012 did not remove the broker; it kept one topic** and specified it:
  "`pcdf.record.processed` is emitted per record, keyed by entity id where
  there is one so an entity's records land on one partition in order."
  `INTERVIEWPREP.md` cited ADR 0012 as the authority for having no broker at
  all, which the ADR does not say.

There was also a real operational gap the removal made worse rather than
better. There is no retry or backoff anywhere in this codebase. A transient
Postgres failure during a watcher sweep sends every file dropped in that window
to `_failed/` permanently and leaves the batch stuck `running`. With the
consumers gone, nothing anywhere held work that could not be done yet.

## Decision 1 — Kafka carries announcements, never values

The batch path is event-driven again. `ingest` publishes
`pcdf.batch.ingested` once its rows are committed, and the five stage consumers
chain from there to `golden.updated`. Every event carries references — a batch
id, an entity id, routing status — and a consumer reads the values back out of
Postgres, so there is one place where what we know about a record lives and an
event can never go stale against it.

The record-at-a-time path is untouched. It still carries one record from raw
row to golden value in a single transaction, and it now also publishes
`pcdf.record.processed` after each commit, for anything downstream that wants
records as they become ready.

## Decision 2 — the two paths are separated structurally, not by convention

`pcdf.batch.ingested` is what starts the consumer chain. The record-at-a-time
path never publishes it, and a test reads the runner's syntax tree to assert
that it publishes `TOPIC_RECORD_PROCESSED` and nothing else.

This matters because ADR 0012 recorded a concurrent-golden-builder collision
"hit by running the batch stages manually while the Kafka consumer chain was
processing the same batch," and left closing it as follow-up. A file loaded
through a watched feed has already reached its golden values by the time it is
announced; handing that same batch to the chain would be two builders on one
entity. `golden.repository.lock_entity` guards the collision itself; this
keeps the situation from arising.

## Decision 3 — publish after commit, and make the gap visible

Rows are committed before anything is published, which makes it structurally
impossible to emit an event referencing a row that does not exist. The cost is
the dual-write: if the broker is unreachable afterwards, rows exist with no
announcement. That is surfaced rather than hidden — `batch.events_published_at`
stays NULL, the ingest CLI exits 4 rather than 0, and both are queryable and
replayable. Postgres is the source of truth; Kafka is a derived notification.

## Decision 4 — a stopped consumer is a queue, not a loss

Offsets are committed only after a batch is processed. A consumer that hits a
transient failure exits without committing, and the container restart resumes
from the last committed offset. A permanent failure for one message — a batch
that cannot be mapped at all — is committed and skipped, because retrying it
forever would stop every message behind it.

Verified by stopping `resolution`, loading a file, and observing lag 1 on
`pcdf.records.validated` with no entities created; starting it drained the
backlog and the golden build followed automatically.

## Decision 5 — liveness is a heartbeat, for consumers as for the watcher

`restart: unless-stopped` only acts on a process that exited. A consumer
blocked on a broker that accepts connections and never delivers keeps its
container "up." Each consumer touches a file on every poll — before the poll,
so an idle consumer and a busy one look alike — and its healthcheck reads the
staleness. `common.heartbeat` holds the one copy of this; the watcher now uses
it too.

## Consequences

- Nine long-running containers instead of three. That is the cost of the
  decoupling, and it is the cost ADR 0001 already accepted.
- The stage CLIs still work. The consumer is a service's default *command*;
  the entrypoint is unchanged, so `docker compose run --rm <stage> <cli>` is
  unaffected and remains the way to redo one stage after a rule change.
- `batch.events_published_at` is live again after being a dead column.
- Still open: the watcher treats a transient Postgres failure as a permanent
  file failure. The consumer chain fixes this for the batch path only. Retry
  with backoff, and distinguishing infrastructure failure from bad data, is
  the next piece of work.
