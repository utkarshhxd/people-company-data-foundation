-- One row per long-running process, saying when it last turned its loop.
--
-- The container healthcheck already reads a file, but a file is only visible
-- inside the container that wrote it: the review console cannot see whether
-- the watcher is sweeping or whether the five consumers are consuming, which
-- is exactly what an operator opens the console to find out. Postgres is the
-- one thing every process already talks to, so it is where the answer goes.
--
-- Deliberately not a log table. There is one row per service, overwritten --
-- the question is "is it alive now", and keeping the history of that would be
-- a time series, which is Prometheus' job and not this table's.

CREATE TABLE service_heartbeat (
    service   text PRIMARY KEY,
    beat_at   timestamptz NOT NULL DEFAULT now(),
    -- Whatever the process wants to say about itself: the topic a consumer is
    -- subscribed to, the directory a watcher is polling. Read by humans, never
    -- branched on.
    detail    jsonb       NOT NULL DEFAULT '{}'::jsonb
);
