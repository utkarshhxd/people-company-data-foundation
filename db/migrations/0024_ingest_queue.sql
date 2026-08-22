-- Files waiting to be loaded, as a table rather than as threads.
--
-- Until now an upload started ingesting the moment it finished arriving, in a
-- thread of its own, tracked in a dictionary in one process. That is fine for
-- one file and wrong for ten: ten uploads meant ten concurrent ingests, ten
-- connections, ten batches interleaving their writes, and a restart in the
-- middle of any of them lost every record of what had been asked for -- the
-- bytes were on disk with nothing left saying what they were meant to be
-- loaded as.
--
-- So the request becomes a row. It is written before any work starts, it
-- survives a restart, everyone looking at the console sees the same queue, and
-- exactly one worker takes exactly one item at a time. The arguments live here
-- too, because "load this as a person feed from Apollo at reliability 0.7" is
-- the part that cannot be recovered from the file itself.
--
-- Nothing here is the source of truth about what was loaded -- `batch` still
-- is. This is the record of what somebody asked for, and what became of the
-- asking.

CREATE TABLE ingest_queue (
    queue_id        uuid PRIMARY KEY DEFAULT uuidv7(),

    -- What the operator dropped, and where the bytes actually went. The stored
    -- path is prefixed with the queue id, so two people dropping the same
    -- filename never collide, and the original name stays readable.
    file_name       text        NOT NULL,
    stored_path     text        NOT NULL,
    size_bytes      bigint      NOT NULL,

    -- The arguments ingestion needs. Same names, same meanings, same
    -- constraints as the CLI flags -- this is a queued invocation, not a
    -- second way of describing a load.
    entity_type     text        NOT NULL CHECK (entity_type IN ('person', 'company')),
    source_name     text        NOT NULL,
    source_type     text        CHECK (source_type IN ('csv', 'excel')),
    record_id_column text,
    reliability     numeric(3,2) NOT NULL DEFAULT 0.5
                    CHECK (reliability BETWEEN 0 AND 1),
    describes       text        CHECK (describes IN ('organisation', 'location')),
    batch_size      integer     NOT NULL CHECK (batch_size > 0),
    allow_reingest  boolean     NOT NULL DEFAULT false,
    sheet           text,

    -- 'held' is not 'failed': it is a queued item the worker declined to start
    -- because ingestion is paused. It goes back to 'queued' the moment
    -- ingestion runs again, and nothing about it is lost in the meantime.
    status          text        NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'held', 'running', 'completed',
                                      'failed', 'cancelled')),

    queued_by       text        NOT NULL,
    queued_at       timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz,
    -- Touched while the worker is actually inside ingest(). A row left
    -- 'running' by a process that died is otherwise indistinguishable from
    -- one being loaded right now, and would sit there forever with its file
    -- never loaded and nothing saying so -- the exact silent loss this
    -- table exists to prevent.
    heartbeat_at    timestamptz,

    -- What came of it. Filled from IngestResult, so the queue can show the
    -- outcome without a second lookup, and still points at the batch for
    -- anyone who wants the real thing.
    batch_id        uuid REFERENCES batch (batch_id),
    rows_read       integer,
    rows_ingested   integer,
    rows_skipped    integer,

    -- Why it did not work, in the words the operator needs. Kept alongside a
    -- structured detail blob for anything worth acting on programmatically --
    -- the sheet names in a multi-sheet workbook, for instance, which are
    -- otherwise only readable out of an error string.
    error           text,
    detail          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    attempts        integer     NOT NULL DEFAULT 0
);

-- The worker's claim query: oldest waiting item first. Partial, because the
-- waiting ones are the only ones it ever looks for and they are the small
-- minority of a table that only grows.
CREATE INDEX ingest_queue_waiting_idx
    ON ingest_queue (queued_at)
    WHERE status IN ('queued', 'held');

-- The console's list: most recent first, which is the order anybody reads a
-- queue's history in.
CREATE INDEX ingest_queue_recent_idx ON ingest_queue (queued_at DESC);

-- Finding the abandoned ones. Tiny partial index over what is normally an
-- empty set: at most one row is running at a time.
CREATE INDEX ingest_queue_running_idx
    ON ingest_queue (heartbeat_at)
    WHERE status = 'running';
