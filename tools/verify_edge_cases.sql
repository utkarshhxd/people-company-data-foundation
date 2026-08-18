-- What the pipeline made of tools/generate_edge_cases.py.
--
-- Each planted row differs from the others in exactly the field it exists to
-- test, so the planted value can be found without being told which column it is
-- in: it is the value that appears once where every other row shares a value.
-- That is what `planted` below does, and it means this file does not have to be
-- kept in step with the generator.
--
--     docker compose exec -T postgres psql -U pcdf_dev -d pcdf \
--         -v batch="'<batch_id>'" -f /tools/verify_edge_cases.sql

\set QUIET on
\pset border 2
\timing off

\echo
\echo ===============================================================
\echo 1. NORMALIZATION -- raw value in, comparable value out
\echo ===============================================================

WITH obs AS (
    SELECT r.source_record_id AS case_id, o.source_column, o.raw_value,
           o.normalized_value, o.normalization_method, o.canonical_field,
           o.mapping_status,
           count(*) OVER (PARTITION BY o.source_column, o.raw_value) AS times_seen
    FROM attribute_observation o
    JOIN raw_record r ON r.record_id = o.record_id
    WHERE o.batch_id = :batch AND r.source_record_id LIKE 'EDGE-%'
)
SELECT case_id,
       source_column                        AS column,
       left(raw_value, 34)                  AS raw,
       left(coalesce(normalized_value, '·· refused ··'), 34) AS normalized,
       normalization_method                 AS method
FROM obs
WHERE times_seen = 1              -- the planted value, not the shared filler
  AND raw_value <> ''
ORDER BY case_id, source_column;

\echo
\echo ===============================================================
\echo 2. VALIDATION -- every verdict the planted values earned
\echo ===============================================================

SELECT r.source_record_id AS case_id,
       v.rule_id, v.severity,
       coalesce(v.canonical_field, '(record)') AS field,
       left(v.message, 58) AS message
FROM validation_result v
JOIN raw_record r ON r.record_id = v.record_id
WHERE v.batch_id = :batch
  AND v.outcome = 'fail'
  AND r.source_record_id LIKE 'EDGE-%'
  AND v.severity IN ('error', 'warning')
ORDER BY r.source_record_id, v.severity, v.rule_id;

\echo
\echo ===============================================================
\echo 3. LOSSLESS CAPTURE -- nothing a source wrote is discarded
\echo ===============================================================

SELECT
    count(*)                                                 AS observations,
    count(*) FILTER (WHERE canonical_field IS NOT NULL)      AS canonical,
    count(*) FILTER (WHERE canonical_field IS NULL)          AS uncanonical,
    count(*) FILTER (WHERE is_null_token)                    AS null_tokens,
    count(DISTINCT source_column)                            AS columns_seen
FROM attribute_observation WHERE batch_id = :batch;

\echo -- unmapped columns are still captured, with their values intact:
SELECT source_column,
       count(*) FILTER (WHERE NOT is_null_token) AS populated,
       min(left(raw_value, 30)) FILTER (WHERE NOT is_null_token) AS example
FROM attribute_observation
WHERE batch_id = :batch AND canonical_field IS NULL
GROUP BY 1 ORDER BY populated DESC;

\echo
\echo ===============================================================
\echo 4. RESOLUTION -- what merged, what deliberately did not
\echo ===============================================================

SELECT r.source_record_id AS case_id,
       left(l.entity_id::text, 8) AS entity,
       l.match_method, l.match_confidence, l.match_status
FROM record_entity_link l
JOIN raw_record r ON r.record_id = l.record_id
WHERE r.batch_id = :batch
  AND (r.source_record_id LIKE 'DUP-%' OR r.source_record_id LIKE 'NEAR-%')
ORDER BY r.source_record_id;

\echo
\echo ===============================================================
\echo 5. THE GOLDEN RECORD -- what a consumer actually reads
\echo ===============================================================

SELECT left(g.company_name, 30) AS company_name,
       g.city, g.state_region, g.postal_code,
       g.employee_count, g.founded_year,
       g.annual_revenue, g.total_funding, g.last_funding_date,
       g.golden_fields, g.mean_confidence
FROM golden_company g
JOIN record_entity_link l ON l.entity_id = g.company_id
JOIN raw_record r ON r.record_id = l.record_id
WHERE r.batch_id = :batch AND r.source_record_id LIKE 'DUP-%'
GROUP BY g.company_name, g.city, g.state_region, g.postal_code,
         g.employee_count, g.founded_year, g.annual_revenue, g.total_funding,
         g.last_funding_date, g.golden_fields, g.mean_confidence;
