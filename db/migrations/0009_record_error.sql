-- Where a record goes when processing it throws.
--
-- Record-at-a-time processing earns its keep here. When a file is written in
-- one transaction, a single unprocessable row rolls back everything and the run
-- is lost. When the record is the unit of work, that row fails alone — but only
-- if there is somewhere for it to land. Without this table the honest options
-- would be to abort the run or to drop the row silently, and dropping it would
-- break the rule the whole system is built on: source information is never lost.
--
-- This is deliberately NOT quarantine. Quarantine holds records the pipeline
-- understood and judged unusable, with reason codes from the ruleset. This
-- holds records the pipeline could not process at all — a malformed payload, a
-- constraint violation, a bug. One is a verdict about the data; the other is a
-- failure of ours, and conflating them would let our own defects masquerade as
-- the vendor's.
--
-- The raw payload is stored here because the record may have failed *before*
-- raw_record was written, so there is no record_id to point at and no other
-- copy of the row inside the database. This table is the only evidence the row
-- ever arrived.

CREATE TABLE record_error (
    error_id         uuid PRIMARY KEY DEFAULT uuidv7(),
    batch_id         uuid NOT NULL REFERENCES batch (batch_id),
    source_id        uuid NOT NULL REFERENCES source (source_id),
    entity_type      text NOT NULL CHECK (entity_type IN ('person', 'company')),

    -- Position in the file, so the row can always be found in the source again.
    row_number       integer NOT NULL,
    source_record_id text,
    raw_payload      jsonb NOT NULL,

    -- Which step was running. Named rather than constrained: stages get added,
    -- and a rejected insert here would lose the very error it is recording.
    stage            text NOT NULL,
    error_type       text NOT NULL,
    error_message    text NOT NULL,

    status           text NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'reprocessed', 'ignored')),
    reviewed_at      timestamptz,
    reviewed_by      text,
    review_note      text,

    occurred_at      timestamptz NOT NULL DEFAULT now(),

    -- Reprocessing a batch should update the row rather than accumulate a new
    -- one per attempt: the current state of that row is what matters.
    UNIQUE (batch_id, row_number)
);

CREATE INDEX record_error_batch_idx ON record_error (batch_id);
CREATE INDEX record_error_status_idx ON record_error (status) WHERE status = 'open';
