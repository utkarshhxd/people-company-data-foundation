-- The warnings and errors every service already prints, somewhere a browser
-- can read them.
--
-- Every process here logs, and every line goes to its container's stdout,
-- which means the answer to "why did that stop" lives behind
-- `docker compose logs <the right one>` -- if you know which one, if you are
-- on the host, and if it has not rotated away. The console can already show
-- what the database knows about *records*; it could show nothing about what
-- the *services* said.
--
-- Deliberately not all of it. INFO is a firehose and Postgres is not a log
-- store: only WARNING and above are kept, which is exactly the set that
-- corresponds to "something went wrong, or something unusual was decided".
-- The table is trimmed to a fixed number of rows by the writer, so it has a
-- ceiling no matter how badly a service misbehaves.
--
-- This is a convenience, never a dependency: a service that cannot write here
-- carries on and its stdout is unaffected. Container logs remain the complete
-- record; this is the part worth surfacing.

CREATE TABLE service_log (
    log_id     bigserial PRIMARY KEY,
    service    text        NOT NULL,
    level      text        NOT NULL,
    -- Numeric too, so "at least this severe" is an index-usable comparison
    -- rather than a CASE over level names.
    level_no   integer     NOT NULL,
    logger     text        NOT NULL,
    message    text        NOT NULL,
    -- Traceback, module and line when there is one. Read by humans.
    detail     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Newest first, which is the only order anybody reads a log in.
CREATE INDEX service_log_recent_idx ON service_log (created_at DESC);
CREATE INDEX service_log_service_idx ON service_log (service, created_at DESC);
CREATE INDEX service_log_level_idx ON service_log (level_no, created_at DESC);
