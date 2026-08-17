-- Ten queries for looking at the data by hand, in pgAdmin or psql.
--
-- Connect pgAdmin to localhost:5433 (NOT 5432 -- that would be a native
-- Postgres install, which has none of this in it), database pcdf, user
-- pcdf_dev. Run them one at a time; each is independent.


-- 1. TRUSTED PEOPLE, WITH ADDRESS
-- The deliverable. One row per person, each value already chosen from whatever
-- competing values the sources offered.
SELECT full_name, first_name, last_name, email, phone,
       job_title, city, state_region, country,
       golden_fields, mean_confidence
FROM golden_person
WHERE email IS NOT NULL
ORDER BY mean_confidence DESC
LIMIT 50;


-- 2. TRUSTED COMPANIES, WITH FULL ADDRESS
-- address_line1 keeps whatever the vendor wrote. Normalization collapses
-- whitespace and nothing else: '#610' and 'Ave NW' carry meaning, so they are
-- left exactly as sent.
SELECT company_name, website, email, phone,
       address_line1, city, state_region, postal_code, country,
       employee_count, founded_year, mean_confidence
FROM golden_company
WHERE address_line1 IS NOT NULL
ORDER BY mean_confidence DESC
LIMIT 50;


-- 3. WHO WORKS WHERE
-- One stored relationship, readable from either end. The employer became a
-- company entity in its own right, so many people collapse onto one company.
SELECT p.full_name, p.first_name, p.last_name, p.job_title, p.email,
       c.company_name, c.website, c.city AS company_city
FROM entity_relationship r
JOIN golden_person  p ON p.person_id  = r.from_entity_id
JOIN golden_company c ON c.company_id = r.to_entity_id
WHERE r.valid_to IS NULL
ORDER BY c.company_name
LIMIT 50;


-- 4. COMPANIES WITH THE MOST EMPLOYEES FOUND
-- Proof the employer resolution is deduplicating rather than making one company
-- per row. Pick an entity_id from here to use in queries 5 and 6.
SELECT c.company_id, c.company_name, c.website, count(*) AS people_found
FROM entity_relationship r
JOIN golden_company c ON c.company_id = r.to_entity_id
WHERE r.valid_to IS NULL
GROUP BY 1, 2, 3
ORDER BY people_found DESC
LIMIT 25;


-- 5. WHY DOES A VALUE SAY THAT?  <-- paste an entity_id below
-- The whole point of the system. Every trusted value, the rule that chose it,
-- how many sources agreed, and what it beat.
SELECT g.canonical_field, g.value, g.confidence, g.strategy,
       g.supporting_sources AS sources_agreed,
       g.competing_values   AS values_rejected,
       g.evidence
FROM golden_attribute g
WHERE g.entity_id = '01a00b2f-39d9-7121-b7de-ae68e6d7885d'   -- AdventHealth; swap for any id from query 4
  AND g.valid_to IS NULL
ORDER BY g.canonical_field;


-- 6. EVERY SOURCE CELL BEHIND ONE ENTITY  <-- paste an entity_id below
-- Walks back from the entity to the actual cells: which vendor, which file,
-- which row, which column, and what it literally said.
SELECT s.source_name, b.file_name, rr.row_number,
       o.source_column, o.canonical_field, o.subject,
       o.raw_value, o.normalized_value
FROM record_entity_link l
JOIN attribute_observation o
  ON o.record_id = l.record_id AND o.subject = l.role
JOIN raw_record rr ON rr.record_id = o.record_id
JOIN batch  b ON b.batch_id  = o.batch_id
JOIN source s ON s.source_id = o.source_id
WHERE l.entity_id = '01a00b2f-39d9-7121-b7de-ae68e6d7885d'   -- AdventHealth; swap for any id from query 4
ORDER BY s.source_name, o.source_column;


-- 7. NOTHING IS LOST: every column of one record, canonical or not
-- The columns showing '-- not canonical --' are kept in full but map to no
-- field. Vendor CRM state (Stage, Lists, Demoed) is the usual reason.
SELECT o.source_column, o.subject,
       COALESCE(o.canonical_field, '-- not canonical --') AS canonical_field,
       o.raw_value, o.normalized_value
FROM attribute_observation o
WHERE o.record_id = (
    SELECT record_id FROM raw_record
    WHERE batch_id = (SELECT batch_id FROM batch
                      WHERE status = 'completed'
                      ORDER BY rows_ingested DESC LIMIT 1)
    LIMIT 1
)
ORDER BY (o.canonical_field IS NULL), o.source_column;


-- 8. WHERE SOURCES DISAGREED
-- A contested field is the interesting one. confidence drops as competitors are
-- rejected, so a low number here is the system telling you it is unsure.
SELECT e.entity_type, g.canonical_field, g.value AS chosen,
       g.confidence, g.strategy, g.competing_values AS rejected
FROM golden_attribute g
JOIN entity e ON e.entity_id = g.entity_id
WHERE g.valid_to IS NULL AND g.competing_values > 0
ORDER BY g.competing_values DESC, g.confidence
LIMIT 50;


-- 9. WHAT NEEDS A HUMAN
-- Three separate queues. Nothing here is broken -- these are the decisions the
-- system deliberately refused to make on its own.
SELECT 'mapping needs review' AS queue, count(*) AS items
FROM column_mapping WHERE mapping_status = 'needs_review'
UNION ALL
SELECT 'quarantined records', count(*)
FROM quarantine_item WHERE status = 'open'
UNION ALL
SELECT 'possible duplicate entities', count(*)
FROM match_candidate WHERE status = 'open'
UNION ALL
SELECT 'rows that failed to process', count(*)
FROM record_error WHERE status = 'open';


-- 10. WHAT EACH VENDOR CONTRIBUTED
-- Reliability is set per source at load time and drives survivorship, so this
-- is also the table that explains why one vendor keeps winning.
SELECT s.source_name, s.reliability,
       count(DISTINCT b.batch_id) AS files,
       count(DISTINCT rr.record_id) AS records,
       count(DISTINCT l.entity_id) AS entities
FROM source s
LEFT JOIN batch b       ON b.source_id = s.source_id AND b.status = 'completed'
LEFT JOIN raw_record rr ON rr.batch_id = b.batch_id
LEFT JOIN record_entity_link l ON l.record_id = rr.record_id
GROUP BY 1, 2
ORDER BY records DESC NULLS LAST;


-- BONUS. WHERE CONFIDENCE IS LOW, AND WHY
-- Worth running after query 4. An employer named by a thousand contacts has a
-- thousand records asserting its address, and the confidence penalty counts
-- each disagreeing record rather than each disagreeing *source* -- so a
-- well-established company can end up reading as less certain than a company
-- one vendor mentioned once. The link count is what makes that visible.
SELECT c.company_name,
       g.canonical_field,
       left(g.value, 40) AS chosen_value,
       g.confidence,
       g.supporting_sources AS agreed,
       g.competing_values   AS rejected,
       (SELECT count(*) FROM record_entity_link l
         WHERE l.entity_id = g.entity_id AND l.role = 'employer') AS records_naming_it
FROM golden_attribute g
JOIN golden_company c ON c.company_id = g.entity_id
WHERE g.valid_to IS NULL AND g.competing_values > 20
ORDER BY g.competing_values DESC
LIMIT 30;
