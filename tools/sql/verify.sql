-- Reconciliation checks. Every one is written to return rows only when
-- something is wrong, so an empty result is the pass condition and nobody has
-- to interpret a number.
--
--     docker compose exec -T postgres psql -U pcdf_dev -d pcdf -f /tools/sql/verify.sql
--
-- These check internal consistency: that what each stage recorded agrees with
-- what every other stage recorded. They cannot check whether the data is true --
-- only the source can say that -- but they will catch a stage dropping rows,
-- double-counting, or attributing a value to the wrong subject.

\echo '=== 1. every completed batch has the rows it claims ==='
SELECT b.batch_id, b.rows_ingested AS claimed,
       (SELECT count(*) FROM raw_record r WHERE r.batch_id = b.batch_id) AS actual
FROM batch b
WHERE b.status = 'completed'
  AND b.rows_ingested <> (SELECT count(*) FROM raw_record r WHERE r.batch_id = b.batch_id);

\echo '=== 2. no record without observations (lossless capture) ==='
SELECT r.record_id, r.row_number
FROM raw_record r
WHERE NOT EXISTS (SELECT 1 FROM attribute_observation o WHERE o.record_id = r.record_id)
LIMIT 10;

\echo '=== 3. every resolvable record reached an entity ==='
SELECT rr.record_id
FROM resolvable_record rr
JOIN batch b ON b.batch_id = rr.batch_id AND b.status = 'completed'
WHERE NOT EXISTS (
    SELECT 1 FROM record_entity_link l
    WHERE l.record_id = rr.record_id AND l.role = 'self'
)
LIMIT 10;

\echo '=== 4. no quarantined record contributes a golden value ==='
SELECT DISTINCT q.record_id
FROM quarantine_item q
JOIN record_entity_link l ON l.record_id = q.record_id
JOIN golden_attribute g ON g.winning_record_id = q.record_id
WHERE q.status IN ('open', 'rejected') AND g.valid_to IS NULL
LIMIT 10;

\echo '=== 5. one current golden value per entity per field ==='
SELECT entity_id, canonical_field, count(*)
FROM golden_attribute WHERE valid_to IS NULL
GROUP BY 1, 2 HAVING count(*) > 1
LIMIT 10;

\echo '=== 6. no entity without a record observing it ==='
SELECT e.entity_id, e.entity_type
FROM entity e
WHERE e.status = 'active'
  AND NOT EXISTS (SELECT 1 FROM record_entity_link l WHERE l.entity_id = e.entity_id)
LIMIT 10;

\echo '=== 7. link role always matches the entity it points at ==='
SELECT l.link_id, l.role, l.entity_type, e.entity_type AS actual
FROM record_entity_link l
JOIN entity e ON e.entity_id = l.entity_id
WHERE l.entity_type <> e.entity_type
   OR (l.role = 'employer' AND e.entity_type <> 'company')
LIMIT 10;

\echo '=== 8. employment always runs person -> company ==='
SELECT r.relationship_id, pe.entity_type AS from_type, ce.entity_type AS to_type
FROM entity_relationship r
JOIN entity pe ON pe.entity_id = r.from_entity_id
JOIN entity ce ON ce.entity_id = r.to_entity_id
WHERE r.relationship_type = 'employed_at'
  AND (pe.entity_type <> 'person' OR ce.entity_type <> 'company')
LIMIT 10;

\echo '=== 9. no employer observation on a company record ==='
SELECT o.observation_id, o.entity_type, o.subject
FROM attribute_observation o
WHERE o.entity_type = 'company' AND o.subject = 'employer'
LIMIT 10;

\echo '=== 10. merged entities are tombstoned, not deleted ==='
SELECT entity_id FROM entity
WHERE status = 'merged' AND merged_into_entity_id IS NULL
LIMIT 10;

\echo '=== 11. no golden value from a record linked in another role ==='
SELECT g.golden_id
FROM golden_attribute g
JOIN record_entity_link l
  ON l.record_id = g.winning_record_id AND l.entity_id = g.entity_id
WHERE g.valid_to IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM attribute_observation o
      WHERE o.record_id = g.winning_record_id
        AND o.canonical_field = g.canonical_field
        AND o.subject = l.role
  )
LIMIT 10;

\echo '=== 12. every open record_error still has its exact payload ==='
SELECT error_id, row_number FROM record_error
WHERE status = 'open' AND raw_payload_bytes IS NULL
LIMIT 10;
