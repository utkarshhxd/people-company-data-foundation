-- A mapping must say WHOSE attribute a column is, not only which field.
--
-- 'Company City' and 'City' on an Apollo person row both mean the canonical
-- field `city`. They are not the same fact: one is where the person is, the
-- other is where their employer is. Without a subject the two collide on one
-- field and the pipeline has to discard one of them.
--
-- Subject belongs on the mapping rather than being derived later because it is
-- decided by the same evidence, at the same time, by the same reviewer. A human
-- correcting 'Company State' is correcting both which field it is and whose it
-- is, and the two answers have to travel together or a correction can be half
-- applied.
--
-- Existing rows are all about the record's own subject, which is what the
-- default says. Nothing here changes an existing mapping's meaning.

ALTER TABLE column_mapping
    ADD COLUMN subject text NOT NULL DEFAULT 'self'
        CHECK (subject IN ('self', 'employer'));

-- The uniqueness that matters is one mapping per column, which is unchanged —
-- a column describes one subject's one field. The index below serves the
-- lookup normalization actually performs: every mapping for a layout, grouped
-- by whose attribute it is.
CREATE INDEX column_mapping_schema_subject_idx
    ON column_mapping (source_schema_id, subject);
