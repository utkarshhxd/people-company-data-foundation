-- One row per (entity, field) the AI enrichment job has ever asked the model
-- about, whatever the answer was.
--
-- This is what makes running the job on a schedule cheap: a batch window that
-- re-asked about every still-missing field every day would re-pay the same
-- declined questions forever. Once a pair is here, it is skipped -- caught up
-- means zero new model calls, not zero rows scanned. An operator who wants a
-- field retried (a better model, a fixed prompt) deletes the row; nothing
-- retries on its own.

CREATE TABLE enrichment_attempt (
    attempt_id        uuid PRIMARY KEY DEFAULT uuidv7(),
    entity_id         uuid NOT NULL REFERENCES entity (entity_id) ON DELETE CASCADE,
    entity_type       text NOT NULL CHECK (entity_type IN ('person', 'company')),
    canonical_field   text NOT NULL,
    -- 'written': the model answered confidently enough to become an
    -- observation. 'declined': it had nothing to add, or nothing confident
    -- enough. 'error': the request itself failed (model unreachable, bad
    -- response) -- distinct from 'declined' so an outage doesn't look
    -- identical to genuine "no opinion" in the counts.
    outcome           text NOT NULL CHECK (outcome IN ('written', 'declined', 'error')),
    model             text NOT NULL,
    stated_confidence numeric(4, 3),
    attempted_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (entity_id, canonical_field)
);

CREATE INDEX enrichment_attempt_entity_idx ON enrichment_attempt (entity_id);
