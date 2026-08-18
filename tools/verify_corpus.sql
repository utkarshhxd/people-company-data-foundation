-- Does the corpus prove what it was built to prove?
--
-- Every section maps to a line in corpus_manifest.csv. The point of the fixture
-- is that these are checks against a stated intention rather than a reading of
-- whatever happened.
--
--     docker compose exec -T postgres psql -U pcdf_dev -d pcdf < tools/verify_corpus.sql

\set QUIET on
\pset border 2

\echo
\echo == A. the corpus landed ==
SELECT s.source_name, s.describes, s.reliability, b.status, b.rows_ingested,
       (SELECT count(*) FROM attribute_observation o WHERE o.batch_id = b.batch_id) AS observations,
       (SELECT count(*) FROM record_entity_link l WHERE l.batch_id = b.batch_id) AS linked
FROM batch b JOIN source s ON s.source_id = b.source_id
ORDER BY b.started_at;

\echo
\echo == B. organisation vs location: one domain, two meanings ==
\echo -- a 'location' source: many premises share a brand domain and must NOT merge
SELECT k.key_value AS shared_domain, count(DISTINCT k.entity_id) AS entities
FROM entity_identity_key k
JOIN entity e ON e.entity_id = k.entity_id
WHERE k.key_type = 'website_domain'
  AND k.key_value IN (SELECT DISTINCT k2.key_value
                      FROM entity_identity_key k2 WHERE k2.key_type = 'website_domain')
  AND k.key_value NOT LIKE 'company%'
GROUP BY 1 HAVING count(DISTINCT k.entity_id) > 1
ORDER BY entities DESC LIMIT 10;

\echo -- an 'organisation' source: one domain, one company (must be exactly 1 each)
SELECT count(*) AS domains_checked,
       count(*) FILTER (WHERE entities = 1) AS resolved_to_one,
       count(*) FILTER (WHERE entities > 1) AS split_wrongly
FROM (
    SELECT k.key_value, count(DISTINCT k.entity_id) AS entities
    FROM entity_identity_key k
    WHERE k.key_type = 'website_domain' AND k.key_value LIKE 'company%'
    GROUP BY 1
) x;

\echo
\echo == C. survivorship: which source won each contested field ==
SELECT g.canonical_field, g.strategy, s.source_name AS won_by,
       count(*) AS entities, round(avg(g.confidence), 3) AS avg_confidence
FROM golden_attribute g
JOIN source s ON s.source_id = g.winning_source_id
WHERE g.valid_to IS NULL AND g.entity_type = 'company'
  AND g.canonical_field IN ('founded_year', 'industry', 'city', 'employee_count',
                            'company_name')
GROUP BY 1, 2, 3 ORDER BY 1, entities DESC;

\echo
\echo == D. the employer projection: company facts sourced from person rows ==
SELECT g.canonical_field, count(*) AS companies, round(avg(g.confidence), 3) AS confidence
FROM golden_attribute g
WHERE g.valid_to IS NULL AND g.entity_type = 'company'
  AND g.canonical_field IN ('annual_revenue', 'total_funding', 'technologies',
                            'keywords', 'seo_description', 'retail_location_count',
                            'last_funding_date', 'latest_funding_stage')
GROUP BY 1 ORDER BY 2 DESC;

\echo
\echo == E. the broken export degraded rather than failed ==
SELECT
    count(*)                                              AS records,
    count(*) FILTER (WHERE v.status = 'valid')            AS valid,
    count(*) FILTER (WHERE v.status = 'warning')          AS warning,
    count(*) FILTER (WHERE v.status = 'invalid')          AS invalid,
    (SELECT count(*) FROM quarantine_item q
     WHERE q.batch_id = b.batch_id AND q.status = 'open') AS quarantined,
    (SELECT count(*) FROM record_entity_link l
     WHERE l.batch_id = b.batch_id)                       AS still_resolved
FROM batch b
JOIN source s ON s.source_id = b.source_id
LEFT JOIN record_validation v ON v.batch_id = b.batch_id
WHERE s.source_name = 'corpus_broken'
GROUP BY b.batch_id;

\echo -- the junk column, and the ranges
SELECT v.rule_id, v.severity, count(*)
FROM validation_result v
JOIN batch b ON b.batch_id = v.batch_id
JOIN source s ON s.source_id = b.source_id
WHERE s.source_name = 'corpus_broken' AND v.outcome = 'fail'
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12;

\echo
\echo == F. mapping: what the vendors' column names became ==
SELECT s.source_name, cm.mapping_status, count(*)
FROM column_mapping cm
JOIN source_schema ss ON ss.source_schema_id = cm.source_schema_id
JOIN source s ON s.source_id = ss.source_id
GROUP BY 1, 2 ORDER BY 1, 2;

\echo -- columns that map to nothing (CRM state should be here; nothing else)
SELECT DISTINCT cm.source_column, s.source_name
FROM column_mapping cm
JOIN source_schema ss ON ss.source_schema_id = cm.source_schema_id
JOIN source s ON s.source_id = ss.source_id
WHERE cm.canonical_field IS NULL
ORDER BY 2, 1;

\echo
\echo == G. totals ==
SELECT
    (SELECT count(*) FROM raw_record)                                   AS records,
    (SELECT count(*) FROM attribute_observation)                        AS observations,
    (SELECT count(*) FROM entity WHERE entity_type = 'company')         AS companies,
    (SELECT count(*) FROM entity WHERE entity_type = 'person')          AS people,
    (SELECT count(*) FROM golden_attribute WHERE valid_to IS NULL)      AS golden_values,
    (SELECT count(*) FROM match_candidate WHERE status = 'open')        AS open_candidates,
    (SELECT count(*) FROM quarantine_item WHERE status = 'open')        AS quarantined;
