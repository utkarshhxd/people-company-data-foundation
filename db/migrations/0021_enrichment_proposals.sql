-- Enrichment stops one step short of where migration 0020 first put it.
--
-- ADR 0014 is explicit: AI may propose, never assert. A model's guess must
-- not become a golden value -- and therefore must not become an
-- attribute_observation, since nothing downstream tells an observation apart
-- from one a vendor's file produced -- without a human confirming it first.
--
-- So a confident answer lands here, in its own queue, the same shape as the
-- three the review-console guide already documents: pending until a human
-- decides, and the decision recorded permanently either way. Only on
-- acceptance does it become a real observation, through the exact same
-- write path a vendor row would use -- which is what finally makes it
-- traceable, survivorship-weighted evidence rather than an assertion with a
-- confidence score attached to make it look like one.

CREATE TABLE enrichment_proposal (
    proposal_id       uuid PRIMARY KEY DEFAULT uuidv7(),
    entity_id         uuid NOT NULL REFERENCES entity (entity_id) ON DELETE CASCADE,
    entity_type       text NOT NULL CHECK (entity_type IN ('person', 'company')),
    canonical_field   text NOT NULL,
    proposed_value    text NOT NULL,
    value_type        text NOT NULL,
    stated_confidence numeric(4, 3) NOT NULL,
    model             text NOT NULL,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'accepted', 'rejected')),
    -- Set once, on acceptance: which observation the proposal became. NULL
    -- for a proposal that was rejected, or has not been decided yet.
    record_id         uuid REFERENCES raw_record (record_id),
    reviewed_at       timestamptz,
    reviewed_by       text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (entity_id, canonical_field)
);

CREATE INDEX enrichment_proposal_status_idx ON enrichment_proposal (status);
