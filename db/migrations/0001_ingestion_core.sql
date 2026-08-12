-- Ingestion core: a source record is an OBSERVATION about a real-world entity,
-- not the entity itself. These tables record what a source literally said and
-- where it came from. Entity resolution (person/company) arrives in a later
-- increment and deliberately has no tables here yet.

-- A logical feed/vendor, not a single file. One source produces many batches.
CREATE TABLE source (
    source_id   uuid PRIMARY KEY DEFAULT uuidv7(),
    source_name text        NOT NULL UNIQUE,
    source_type text        NOT NULL CHECK (source_type IN ('csv', 'excel')),
    -- Source reliability is its own dimension: never conflate it with mapping
    -- confidence, validation result, or match confidence.
    reliability numeric(3, 2) NOT NULL DEFAULT 0.50
                CHECK (reliability >= 0 AND reliability <= 1),
    description text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- One ingestion run of one file.
CREATE TABLE batch (
    batch_id            uuid PRIMARY KEY DEFAULT uuidv7(),
    source_id           uuid        NOT NULL REFERENCES source (source_id),
    entity_type         text        NOT NULL CHECK (entity_type IN ('person', 'company')),
    file_path           text        NOT NULL,
    file_name           text        NOT NULL,
    file_hash           text        NOT NULL,
    file_size_bytes     bigint      NOT NULL,
    status              text        NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'completed', 'failed')),
    rows_read           integer     NOT NULL DEFAULT 0,
    rows_ingested       integer     NOT NULL DEFAULT 0,
    rows_skipped        integer     NOT NULL DEFAULT 0,
    error_message       text,
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    -- NULL means the rows are committed but their Kafka events were never
    -- published (e.g. broker down). Postgres is the source of truth; this
    -- column makes the gap visible instead of silent, and enables replay.
    events_published_at timestamptz
);

-- One source row, preserved verbatim.
CREATE TABLE raw_record (
    record_id        uuid PRIMARY KEY DEFAULT uuidv7(),
    batch_id         uuid        NOT NULL REFERENCES batch (batch_id),
    source_id        uuid        NOT NULL REFERENCES source (source_id),
    entity_type      text        NOT NULL CHECK (entity_type IN ('person', 'company')),
    -- Defaults to the row number; set from a natural key column when the file
    -- has one. Only unique within a batch unless a natural key was supplied.
    source_record_id text        NOT NULL,
    row_number       integer     NOT NULL,
    payload_hash     text        NOT NULL,
    raw_payload      jsonb       NOT NULL,
    ingested_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX raw_record_batch_id_idx ON raw_record (batch_id);
CREATE INDEX raw_record_source_record_idx ON raw_record (source_id, source_record_id);
CREATE INDEX raw_record_payload_hash_idx ON raw_record (payload_hash);
CREATE INDEX batch_source_file_hash_idx ON batch (source_id, file_hash);
