-- Entity resolution: the point of the whole pipeline.
--
-- Everything before this stored what sources SAID. This is where records become
-- observations OF something: a stable person_id / company_id that survives the
-- attributes changing, the vendor changing, and the record being superseded.
--
-- Match confidence is its own dimension and must never be conflated with
-- mapping confidence, validation result, or source reliability. A perfectly
-- mapped, fully valid record from a trusted vendor can still be a weak match.

-- The real-world thing. Deliberately holds NO attributes: which name or address
-- is 'the' one is the golden record's decision, in a later increment. An entity
-- is an identity, not a row of values.
CREATE TABLE entity (
    entity_id             uuid PRIMARY KEY DEFAULT uuidv7(),
    entity_type           text        NOT NULL CHECK (entity_type IN ('person', 'company')),
    -- An entity id is never deleted and never reused. When two entities turn
    -- out to be the same, the absorbed one stays here as a tombstone pointing
    -- at the survivor, so any id ever handed out still resolves.
    status                text        NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'merged')),
    merged_into_entity_id uuid REFERENCES entity (entity_id),
    first_seen_record_id  uuid REFERENCES raw_record (record_id),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'merged') = (merged_into_entity_id IS NOT NULL))
);

CREATE INDEX entity_type_status_idx ON entity (entity_type, status);

-- The spec speaks in terms of person_id and company_id; one table keeps the
-- merge and link logic single-pathed, and these views give the vocabulary back.
CREATE VIEW person AS
SELECT entity_id AS person_id, first_seen_record_id, created_at, updated_at
FROM entity WHERE entity_type = 'person' AND status = 'active';

CREATE VIEW company AS
SELECT entity_id AS company_id, first_seen_record_id, created_at, updated_at
FROM entity WHERE entity_type = 'company' AND status = 'active';

-- Which entity a source record is an observation of. One record observes one
-- entity of the batch's type; the employer named inside a person row is a
-- separate relationship and is not modelled here yet.
CREATE TABLE record_entity_link (
    link_id          uuid PRIMARY KEY DEFAULT uuidv7(),
    record_id        uuid          NOT NULL UNIQUE REFERENCES raw_record (record_id) ON DELETE CASCADE,
    entity_id        uuid          NOT NULL REFERENCES entity (entity_id),
    entity_type      text          NOT NULL CHECK (entity_type IN ('person', 'company')),
    batch_id         uuid          NOT NULL REFERENCES batch (batch_id),
    source_id        uuid          NOT NULL REFERENCES source (source_id),
    -- How the link was decided, and how strongly. 'new_entity' means nothing
    -- matched, which is a decision worth recording as much as a match is.
    match_method     text          NOT NULL,
    match_confidence numeric(4, 3) NOT NULL CHECK (match_confidence >= 0 AND match_confidence <= 1),
    match_status     text          NOT NULL
                     CHECK (match_status IN ('auto_linked', 'new_entity', 'manual')),
    evidence         jsonb         NOT NULL DEFAULT '{}'::jsonb,
    linked_at        timestamptz   NOT NULL DEFAULT now(),
    updated_at       timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX record_entity_link_entity_idx ON record_entity_link (entity_id);
CREATE INDEX record_entity_link_batch_idx ON record_entity_link (batch_id);

-- Blocking keys: the values that make an entity findable. Candidate generation
-- reads only this table, so matching never scans every entity.
CREATE TABLE entity_identity_key (
    key_id           uuid PRIMARY KEY DEFAULT uuidv7(),
    entity_id        uuid        NOT NULL REFERENCES entity (entity_id) ON DELETE CASCADE,
    entity_type      text        NOT NULL CHECK (entity_type IN ('person', 'company')),
    key_type         text        NOT NULL,
    key_value        text        NOT NULL,
    -- 'strong' keys can carry a link on their own; 'moderate' ones never can,
    -- which is what stops two different people at one company from merging.
    strength         text        NOT NULL CHECK (strength IN ('strong', 'moderate')),
    source_record_id uuid REFERENCES raw_record (record_id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (entity_id, key_type, key_value)
);

CREATE INDEX entity_identity_key_lookup_idx
    ON entity_identity_key (entity_type, key_type, key_value);

-- A match that scored high enough to be worth a human's attention but not high
-- enough to act on. The record still gets its own entity in the meantime, so
-- the pipeline never blocks — accepting the candidate merges them.
CREATE TABLE match_candidate (
    candidate_id     uuid PRIMARY KEY DEFAULT uuidv7(),
    record_id        uuid          NOT NULL REFERENCES raw_record (record_id) ON DELETE CASCADE,
    -- The entity this record MIGHT also be.
    entity_id        uuid          NOT NULL REFERENCES entity (entity_id) ON DELETE CASCADE,
    entity_type      text          NOT NULL CHECK (entity_type IN ('person', 'company')),
    match_method     text          NOT NULL,
    match_confidence numeric(4, 3) NOT NULL CHECK (match_confidence >= 0 AND match_confidence <= 1),
    evidence         jsonb         NOT NULL DEFAULT '{}'::jsonb,
    status           text          NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'accepted', 'rejected')),
    reviewed_at      timestamptz,
    reviewed_by      text,
    review_note      text,
    created_at       timestamptz   NOT NULL DEFAULT now(),
    UNIQUE (record_id, entity_id)
);

CREATE INDEX match_candidate_status_idx ON match_candidate (status, match_confidence DESC);

-- Append-only record of every merge. Merging is the one irreversible-looking
-- operation here, so what was absorbed, into what, by whom and why is kept
-- permanently — and the absorbed id keeps resolving via entity.merged_into.
CREATE TABLE entity_merge (
    merge_id            uuid PRIMARY KEY DEFAULT uuidv7(),
    merged_entity_id    uuid        NOT NULL REFERENCES entity (entity_id),
    surviving_entity_id uuid        NOT NULL REFERENCES entity (entity_id),
    entity_type         text        NOT NULL CHECK (entity_type IN ('person', 'company')),
    match_confidence    numeric(4, 3),
    records_moved       integer     NOT NULL DEFAULT 0,
    keys_moved          integer     NOT NULL DEFAULT 0,
    actor               text        NOT NULL,
    reason              text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (merged_entity_id <> surviving_entity_id)
);

CREATE INDEX entity_merge_surviving_idx ON entity_merge (surviving_entity_id);

-- Every source record that observes an entity, with the vendor that said it.
-- This is the answer to "what do we know about this person, and who told us" —
-- the cross-dataset link the whole design exists to make possible.
CREATE VIEW entity_observation AS
SELECT l.entity_id,
       l.entity_type,
       o.canonical_field,
       o.normalized_value,
       o.raw_value,
       o.source_column,
       s.source_name,
       s.reliability AS source_reliability,
       b.file_name,
       o.observed_at,
       l.record_id
FROM record_entity_link l
JOIN attribute_observation o ON o.record_id = l.record_id
JOIN source s ON s.source_id = o.source_id
JOIN batch b ON b.batch_id = o.batch_id;
