-- Normalization output: one row per source column per source row.
--
-- This table is the lossless substrate. EVERY column of EVERY ingested row
-- lands here, including columns that mapped to nothing — an unmapped column is
-- still a fact about the source, and vendor files routinely carry useful ones
-- (FAX_NUMBER, CAT, SIC_DESCRIPTION, email_status, profile_pic). Rows are only
-- ever appended; a later observation never overwrites an earlier one. Deciding
-- which competing observation wins is the golden record's job, not this table's.

-- Mapping gained a method that names a likely field without auto-accepting it.
ALTER TABLE column_mapping DROP CONSTRAINT column_mapping_mapping_method_check;
ALTER TABLE column_mapping ADD CONSTRAINT column_mapping_mapping_method_check
    CHECK (mapping_method IN ('exact_alias', 'ambiguous_alias', 'similarity',
                              'value_analysis', 'corroborated', 'manual', 'none'));

CREATE TABLE attribute_observation (
    observation_id   uuid PRIMARY KEY DEFAULT uuidv7(),
    record_id        uuid NOT NULL REFERENCES raw_record (record_id) ON DELETE CASCADE,
    batch_id         uuid NOT NULL REFERENCES batch (batch_id),
    source_id        uuid NOT NULL REFERENCES source (source_id),
    entity_type      text NOT NULL CHECK (entity_type IN ('person', 'company')),

    -- Always retained, even when nothing canonical was matched.
    source_column    text NOT NULL,
    -- NULL when the column is unmapped, or when its mapping is still awaiting
    -- review: an unconfirmed guess must not silently become canonical truth.
    canonical_field  text,
    mapping_status   text NOT NULL,

    -- A single cell can hold several values (the '^^' delimiter shows up in
    -- real vendor exports). Each becomes its own observation rather than being
    -- collapsed into one string.
    value_index      int  NOT NULL DEFAULT 0,

    raw_value        text NOT NULL,
    normalized_value text,
    value_type       text NOT NULL,
    normalization_method text NOT NULL,
    -- Source wrote a placeholder such as 'NULL', 'N/A', '-'. The raw string is
    -- kept verbatim; normalized_value is NULL so downstream doesn't treat the
    -- placeholder as a real value.
    is_null_token    boolean NOT NULL DEFAULT false,

    observed_at      timestamptz NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),

    UNIQUE (record_id, source_column, value_index)
);

-- Entity resolution will look up candidates by canonical identifier values
-- (email, phone, domain), so index that access path now.
CREATE INDEX attribute_observation_field_value_idx
    ON attribute_observation (canonical_field, normalized_value)
    WHERE normalized_value IS NOT NULL;
CREATE INDEX attribute_observation_record_idx ON attribute_observation (record_id);
CREATE INDEX attribute_observation_batch_idx ON attribute_observation (batch_id);
