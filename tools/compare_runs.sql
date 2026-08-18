-- The same 500 rows, before and after a fix.
--
-- Loading a file twice under two source names is a cheap way to prove a change
-- did what was claimed: the input is byte-identical, so every difference is the
-- code. Kept because "the tests pass" and "the pipeline now does the right thing
-- to a real file" are different claims, and only the second one is the point.
--
--     docker compose exec -T postgres psql -U pcdf_dev -d pcdf \
--         -v before="'<batch>'" -v after="'<batch>'" < tools/compare_runs.sql

\set QUIET on
\pset border 2

\echo
\echo == canonical coverage: how much of the file became usable ==
SELECT run, total, canonical, uncanonical,
       round(100.0 * canonical / total, 1) AS pct_canonical
FROM (
    SELECT 'before' AS run, count(*) AS total,
           count(*) FILTER (WHERE canonical_field IS NOT NULL) AS canonical,
           count(*) FILTER (WHERE canonical_field IS NULL)     AS uncanonical
    FROM attribute_observation WHERE batch_id = :before
    UNION ALL
    SELECT 'after', count(*),
           count(*) FILTER (WHERE canonical_field IS NOT NULL),
           count(*) FILTER (WHERE canonical_field IS NULL)
    FROM attribute_observation WHERE batch_id = :after
) x ORDER BY run DESC;

\echo
\echo == columns that map to nothing, per run ==
SELECT coalesce(b.source_column, a.source_column) AS source_column,
       coalesce(b.n, 0) AS before, coalesce(a.n, 0) AS after
FROM (SELECT source_column, count(*) n FROM attribute_observation
      WHERE batch_id = :before AND canonical_field IS NULL GROUP BY 1) b
FULL JOIN (SELECT source_column, count(*) n FROM attribute_observation
           WHERE batch_id = :after AND canonical_field IS NULL GROUP BY 1) a
  USING (source_column)
ORDER BY before DESC NULLS LAST;

\echo
\echo == the two defects, on the rows that carry them ==
SELECT r.source_record_id AS case_id,
       CASE WHEN o.batch_id = :before THEN 'before' ELSE 'after' END AS run,
       o.source_column, o.raw_value,
       coalesce(o.normalized_value, '·· refused ··') AS normalized,
       o.normalization_method AS method
FROM attribute_observation o
JOIN raw_record r ON r.record_id = o.record_id
WHERE o.batch_id IN (:before, :after)
  AND r.source_record_id IN ('EDGE-006', 'EDGE-024', 'EDGE-025', 'EDGE-026', 'EDGE-029')
  AND o.source_column IN ('mobile_no', '# Employees')
ORDER BY r.source_record_id, run DESC;

\echo
\echo == verdicts that only fire once a value is canonical ==
SELECT CASE WHEN v.batch_id = :before THEN 'before' ELSE 'after' END AS run,
       v.rule_id, v.severity, count(*)
FROM validation_result v
WHERE v.batch_id IN (:before, :after) AND v.outcome = 'fail'
  AND v.rule_id IN ('phone.digit_count', 'phone.filler', 'phone.country_code',
                    'value.empty_after_normalization', 'value.range_given',
                    'integer.input')
GROUP BY 1, 2, 3 ORDER BY v.rule_id, run DESC;

\echo
\echo == employee_count as a consumer would read it ==
SELECT CASE WHEN o.batch_id = :before THEN 'before' ELSE 'after' END AS run,
       g.value AS golden_employee_count, g.confidence
FROM attribute_observation o
JOIN raw_record r ON r.record_id = o.record_id
JOIN record_entity_link l ON l.record_id = r.record_id
JOIN golden_attribute g ON g.entity_id = l.entity_id
                       AND g.canonical_field = 'employee_count'
                       AND g.valid_to IS NULL
WHERE o.batch_id IN (:before, :after) AND r.source_record_id = 'EDGE-029'
  AND o.source_column = '# Employees'
ORDER BY run DESC;
