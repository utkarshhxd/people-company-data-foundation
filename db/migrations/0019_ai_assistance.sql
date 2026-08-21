-- AI assistance, two narrow slots:
--
--   * schema mapping gains a method a local model may propose when the
--     deterministic rules leave a column below auto-accept. It is scored and
--     capped exactly like value_analysis -- a proposal, never a confirmation --
--     and lands in the same needs_review queue a human already works.
--   * enrichment gains a source: a synthetic vendor the AI enrichment job
--     writes observations through, at a low, adjustable reliability. It goes
--     through the exact same batch / raw_record / attribute_observation /
--     record_entity_link tables a real vendor file does, so golden-record
--     survivorship, review and lineage all already know how to handle it.
--
-- Neither slot changes what happens to a deployment that never runs the AI
-- path: both are opt-in (ai_mapping_enabled, and the enrichment job is
-- CLI-only), and this migration only widens what the schema *allows*.

ALTER TABLE column_mapping DROP CONSTRAINT column_mapping_mapping_method_check;
ALTER TABLE column_mapping ADD CONSTRAINT column_mapping_mapping_method_check
    CHECK (mapping_method IN ('exact_alias', 'ambiguous_alias', 'similarity',
                              'value_analysis', 'corroborated', 'manual',
                              'ai_suggestion', 'none'));

ALTER TABLE source DROP CONSTRAINT source_source_type_check;
ALTER TABLE source ADD CONSTRAINT source_source_type_check
    CHECK (source_type IN ('csv', 'excel', 'ai_enrichment'));
