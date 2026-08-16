-- The rest of the unindexed foreign keys worth indexing.
--
-- Migration 0008 covered the four pointing at raw_record. A wider audit found
-- twelve more. This migration takes seven of them and deliberately leaves five,
-- because "index every foreign key" is a rule of thumb, not a reason.
--
-- The one that forced the issue: golden_attribute.superseded_by references
-- golden_attribute itself. Removing golden rows therefore scanned the whole
-- golden table once per row deleted. A cleanup of one 20,000-record run was
-- still running after twenty minutes.
--
-- Indexed here, each because it is a real access path and not only a delete:

-- Self-reference. Also "what did this value replace", walked when reading the
-- history of a golden value.
CREATE INDEX golden_attribute_superseded_by_idx
    ON golden_attribute (superseded_by);

-- The event history of one quarantined record, read every time a record's
-- quarantine decisions are explained.
CREATE INDEX quarantine_event_quarantine_idx
    ON quarantine_event (quarantine_id);

-- Merge tombstones are followed forward on every lookup of an id that has been
-- merged away, which is the promise that old ids keep resolving.
CREATE INDEX entity_merged_into_idx
    ON entity (merged_into_entity_id)
    WHERE merged_into_entity_id IS NOT NULL;

-- "Which records were proposed as matches for this entity" — the review queue's
-- own question.
CREATE INDEX match_candidate_entity_idx
    ON match_candidate (entity_id);

-- The other half of a merge record; surviving_entity_id is already indexed.
CREATE INDEX entity_merge_merged_idx
    ON entity_merge (merged_entity_id);

-- Which batch first showed us a column layout.
CREATE INDEX source_schema_first_seen_batch_idx
    ON source_schema (first_seen_batch_id);

-- Deliberately NOT indexed: source_id on attribute_observation,
-- record_validation, quarantine_item, record_entity_link and record_error.
--
-- There are a handful of sources and millions of observations, so these columns
-- have almost no selectivity — an index on them would rarely be chosen for a
-- read, and attribute_observation takes ten inserts per record, where the write
-- cost is paid on every single one. The only thing such an index would buy is a
-- faster DELETE FROM source, and sources are not deleted: a source is the
-- provenance of everything under it. Anything genuinely scoped to a source is
-- reached through batch, which is indexed.
