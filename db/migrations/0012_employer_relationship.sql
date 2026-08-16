-- A person row names their employer. Model the employer as a company entity.
--
-- Vendor person exports (Apollo, c_suite, LeadsNemo) carry two subjects in one
-- row: the person, and the company they work for. Until now the employer's
-- columns were captured as observations and went no further, so the same
-- employer appearing in five hundred rows was five hundred unlinked strings
-- and "who works at Gilbane" had no answer.
--
-- The row stays ONE record. A source row is one row: splitting it into two
-- records would fork its provenance, make row_number ambiguous, and double
-- what record_error means. What gains a role is the LINK, not the record.
--
--   raw_record  ─┬─ observations subject='self'      → person entity  (role 'self')
--                └─ observations subject='employer'  → company entity (role 'employer')
--                                                      + entity_relationship
--
-- Nothing here changes an existing row's meaning: every current observation is
-- about the record's own subject, and every current link is the record's own
-- entity, which is exactly what the defaults below say.

-- Which subject of the record this observation describes. 'self' is the entity
-- the record is fundamentally about; 'employer' is the company named inside a
-- person row.
ALTER TABLE attribute_observation
    ADD COLUMN subject text NOT NULL DEFAULT 'self'
        CHECK (subject IN ('self', 'employer'));

-- Reads are always "this record's observations about this subject", because a
-- company entity's golden values must never be built from the person's own
-- address.
CREATE INDEX attribute_observation_record_subject_idx
    ON attribute_observation (record_id, subject);

-- The link gains the same role. Dropping the UNIQUE on record_id is the whole
-- point of this migration: one record may now observe a person AND a company.
-- The replacement constraint keeps the guarantee that actually mattered — a
-- record cannot be linked twice in the same role.
ALTER TABLE record_entity_link
    ADD COLUMN role text NOT NULL DEFAULT 'self'
        CHECK (role IN ('self', 'employer'));

ALTER TABLE record_entity_link
    DROP CONSTRAINT record_entity_link_record_id_key;

ALTER TABLE record_entity_link
    ADD CONSTRAINT record_entity_link_record_role_key UNIQUE (record_id, role);

-- An employer link points at a company whatever the record's own type is, so
-- the existing (record_id) lookups that now need to stay single-valued must
-- say which role they mean.
CREATE INDEX record_entity_link_entity_role_idx
    ON record_entity_link (entity_id, role);

-- Relationships between entities. Only 'employed_at' is produced today, but the
-- shape is general because the next one (subsidiary_of, same_household) would
-- otherwise arrive as a second table saying the same thing.
CREATE TABLE entity_relationship (
    relationship_id   uuid PRIMARY KEY DEFAULT uuidv7(),
    from_entity_id    uuid        NOT NULL REFERENCES entity (entity_id),
    to_entity_id      uuid        NOT NULL REFERENCES entity (entity_id),
    relationship_type text        NOT NULL CHECK (relationship_type IN ('employed_at')),
    -- The record that asserted it, which is what makes the relationship
    -- explainable and lets it be attributed to a vendor like everything else.
    record_id         uuid        NOT NULL REFERENCES raw_record (record_id) ON DELETE CASCADE,
    batch_id          uuid        NOT NULL REFERENCES batch (batch_id),
    source_id         uuid        NOT NULL REFERENCES source (source_id),
    -- Current relationships have valid_to IS NULL, matching golden_attribute's
    -- convention. A person changing employer closes the old row rather than
    -- overwriting it: where someone used to work is not a mistake to erase.
    valid_from        timestamptz NOT NULL DEFAULT now(),
    valid_to          timestamptz,
    evidence          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (from_entity_id <> to_entity_id)
);

-- One current relationship of a given type between two entities. Asserting the
-- same employment from a second vendor must update, not duplicate.
CREATE UNIQUE INDEX entity_relationship_current_key
    ON entity_relationship (from_entity_id, to_entity_id, relationship_type)
    WHERE valid_to IS NULL;

-- Both directions are asked for: "where does this person work" and "who works
-- here". Foreign keys are indexed from the referencing side, which Postgres
-- does not do on its own.
CREATE INDEX entity_relationship_from_idx ON entity_relationship (from_entity_id)
    WHERE valid_to IS NULL;
CREATE INDEX entity_relationship_to_idx ON entity_relationship (to_entity_id)
    WHERE valid_to IS NULL;
CREATE INDEX entity_relationship_record_idx ON entity_relationship (record_id);
CREATE INDEX entity_relationship_batch_idx ON entity_relationship (batch_id);
CREATE INDEX entity_relationship_source_idx ON entity_relationship (source_id);
