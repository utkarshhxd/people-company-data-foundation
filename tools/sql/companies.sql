-- Reading company golden records back out.
--
--   docker compose exec -T postgres psql -U pcdf_dev -d pcdf -f /tools/sql/companies.sql
--   kubectl exec -i -n pcdf postgres-0 -- psql -U pcdf_dev -d pcdf < tools/sql/companies.sql
--
-- One filter matters more than anything else here: `valid_to IS NULL`.
-- golden_attribute keeps every value it ever chose, superseding rather than
-- overwriting, so without that filter every query below returns the history as
-- well as the present. The unique index
-- (entity_id, canonical_field) WHERE valid_to IS NULL is the shape of "current".


-- 1. Companies as a flat table, one row each.
--
-- The golden record is stored one row per field, which is what makes it
-- traceable and what makes it awkward to read. This pivots it. Add a field by
-- adding a line -- see `select distinct canonical_field from golden_attribute`
-- for what a company can carry.
SELECT
    e.entity_id,
    MAX(g.value) FILTER (WHERE g.canonical_field = 'company_name')   AS company_name,
    MAX(g.value) FILTER (WHERE g.canonical_field = 'website')        AS website,
    MAX(g.value) FILTER (WHERE g.canonical_field = 'industry')       AS industry,
    MAX(g.value) FILTER (WHERE g.canonical_field = 'city')           AS city,
    MAX(g.value) FILTER (WHERE g.canonical_field = 'state_region')   AS state_region,
    MAX(g.value) FILTER (WHERE g.canonical_field = 'country')        AS country,
    MAX(g.value) FILTER (WHERE g.canonical_field = 'employee_count') AS employee_count,
    MAX(g.value) FILTER (WHERE g.canonical_field = 'phone')          AS phone,
    MAX(g.value) FILTER (WHERE g.canonical_field = 'email')          AS email
FROM entity e
JOIN golden_attribute g
  ON g.entity_id = e.entity_id
 AND g.valid_to IS NULL
WHERE e.entity_type = 'company'
  AND e.status = 'active'
GROUP BY e.entity_id
ORDER BY company_name
LIMIT 50;


-- 2. One company, every field, with where each value came from.
--
-- This is the question the whole system exists to answer: not "what is this
-- company's phone number" but "which vendor, which file, which row, which
-- column said so, and what did the others say". Replace the entity_id.
SELECT
    g.canonical_field                AS field,
    g.value,
    g.confidence,
    g.strategy,                      -- the rule that picked this value
    g.supporting_sources             AS sources_agreeing,
    g.competing_values               AS values_seen,
    s.source_name,
    o.source_column,                 -- the column header in the vendor's file
    o.raw_value                      AS source_cell
FROM golden_attribute g
LEFT JOIN source s
       ON s.source_id = g.winning_source_id
LEFT JOIN attribute_observation o
       ON o.record_id = g.winning_record_id
      AND o.canonical_field = g.canonical_field
WHERE g.valid_to IS NULL
  AND g.entity_id = '00000000-0000-0000-0000-000000000000'  -- <- entity_id here
ORDER BY g.canonical_field;


-- 3. Find a company by name, to get an entity_id for the query above.
SELECT g.entity_id, g.value AS company_name, g.confidence
FROM golden_attribute g
WHERE g.valid_to IS NULL
  AND g.entity_type = 'company'
  AND g.canonical_field = 'company_name'
  AND g.value ILIKE '%acme%'    -- <- search term here
ORDER BY g.value
LIMIT 20;


-- 4. Where the record disagreed with itself.
--
-- competing_values > 1 means several sources reported different values for the
-- same field and survivorship had to choose. These are the rows worth a human
-- glance, and the ones that justify keeping the losers.
SELECT
    name.value                AS company_name,
    g.canonical_field         AS field,
    g.value                   AS chosen,
    g.strategy,
    g.competing_values        AS values_seen
FROM golden_attribute g
JOIN golden_attribute name
  ON name.entity_id = g.entity_id
 AND name.canonical_field = 'company_name'
 AND name.valid_to IS NULL
WHERE g.valid_to IS NULL
  AND g.entity_type = 'company'
  AND g.competing_values > 1
ORDER BY g.competing_values DESC, name.value
LIMIT 40;


-- 5. Every value ever reported for one field of one company, winners and
-- losers together -- including the ones survivorship rejected, which are kept
-- rather than discarded and are the reason this system exists.
SELECT
    o.raw_value,
    o.normalized_value,
    o.source_column,
    s.source_name,
    s.reliability,
    r.record_id
FROM record_entity_link l
JOIN raw_record r              ON r.record_id = l.record_id
JOIN attribute_observation o   ON o.record_id = r.record_id
JOIN source s                  ON s.source_id = r.source_id
WHERE l.entity_id = '00000000-0000-0000-0000-000000000000'  -- <- entity_id here
  AND o.canonical_field = 'employee_count'                  -- <- field here
ORDER BY s.reliability DESC;
