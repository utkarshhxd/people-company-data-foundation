-- Schema mapping: which canonical field does each source column represent?
-- Recorded per source SCHEMA (a column layout), not per batch, so a mapping —
-- including any human correction — is reused the next time the same source
-- sends the same columns.

CREATE TABLE source_schema (
    source_schema_id   uuid PRIMARY KEY DEFAULT uuidv7(),
    source_id          uuid        NOT NULL REFERENCES source (source_id),
    -- The batch this layout was first observed in; later batches with the same
    -- fingerprint reuse this row rather than creating another.
    first_seen_batch_id uuid       NOT NULL REFERENCES batch (batch_id),
    entity_type        text        NOT NULL CHECK (entity_type IN ('person', 'company')),
    -- sha256 of the ordered column list: identifies "this exact layout".
    column_fingerprint text        NOT NULL,
    columns            jsonb       NOT NULL,
    schema_version     text        NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, column_fingerprint, schema_version)
);

CREATE TABLE column_mapping (
    mapping_id         uuid PRIMARY KEY DEFAULT uuidv7(),
    source_schema_id   uuid        NOT NULL REFERENCES source_schema (source_schema_id)
                       ON DELETE CASCADE,
    source_column      text        NOT NULL,
    -- NULL when nothing matched well enough. An unmapped column is still a
    -- fact about the source and is never dropped.
    canonical_field    text,
    mapping_method     text        NOT NULL
                       CHECK (mapping_method IN ('exact_alias', 'similarity',
                                                 'value_analysis', 'corroborated',
                                                 'manual', 'none')),
    -- Mapping confidence only. Deliberately NOT the same thing as source
    -- reliability, validation result, or match confidence.
    mapping_confidence numeric(4, 3) NOT NULL DEFAULT 0
                       CHECK (mapping_confidence >= 0 AND mapping_confidence <= 1),
    mapping_status     text        NOT NULL
                       CHECK (mapping_status IN ('auto_accepted', 'needs_review',
                                                 'unmapped', 'approved', 'rejected')),
    -- Why this decision was made: matched alias, similarity score, value
    -- samples, rejected alternatives, collision notes.
    evidence           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    schema_version     text        NOT NULL,
    reviewed_at        timestamptz,
    reviewed_by        text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_schema_id, source_column)
);

CREATE INDEX column_mapping_status_idx ON column_mapping (mapping_status);
CREATE INDEX source_schema_source_idx ON source_schema (source_id);
