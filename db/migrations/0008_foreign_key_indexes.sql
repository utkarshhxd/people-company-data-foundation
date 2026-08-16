-- Index the foreign keys that had no index.
--
-- Found while measuring the record-at-a-time pipeline: removing 50,000
-- attribute_observation rows took over eleven minutes. Postgres indexes the
-- side a foreign key points *to* (that is the primary key) but never the side
-- it points *from*, so every delete of a referenced row has to prove no child
-- still references it. With no index on the child column that proof is a
-- sequential scan of the whole child table, once per deleted row.
--
-- Four columns were in that state. They are also ordinary query paths — "which
-- records back this entity", "which record won this golden value" — so the
-- indexes earn their keep on reads whether or not anything is ever deleted.
--
-- Nothing in this system deletes source data by design, and that does not
-- change: invalid records must not disappear. But reprocessing, correcting a
-- bad load, and a merge rewriting an entity's golden values all touch these
-- paths, and none of them should be quadratic.

-- Referenced by validation: 499,724 rows scanned per observation removed.
CREATE INDEX validation_result_observation_idx
    ON validation_result (observation_id);

-- "Which record first told us this entity exists."
CREATE INDEX entity_first_seen_record_idx
    ON entity (first_seen_record_id);

-- "Which record contributed this identity key" — read on every explain of a
-- match decision.
CREATE INDEX entity_identity_key_source_record_idx
    ON entity_identity_key (source_record_id);

-- "Which record won this golden value" — the provenance join in common.lineage,
-- served by both the API and `golden explain`.
CREATE INDEX golden_attribute_winning_record_idx
    ON golden_attribute (winning_record_id);
