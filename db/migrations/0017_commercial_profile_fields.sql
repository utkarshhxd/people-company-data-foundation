-- The commercial-profile fields, and the index that would have refused them.
--
-- Two changes, both forced by the same fact: `technologies`, `keywords` and
-- `seo_description` hold long text. The longest Technologies cell in the Apollo
-- export is 2,960 characters.
--
-- 1. golden_attribute_lookup_idx indexes `value` directly, and a btree tuple
--    cannot exceed 2704 bytes. The first golden build that chose a Technologies
--    value would have failed with
--        index row size ... exceeds btree version 4 maximum 2704
--    and taken the whole entity build down with it. This is the same shape of
--    failure as migration 0014, which dropped an observation index for exactly
--    this reason; the difference is that this one is used, so it is rebuilt on a
--    prefix rather than dropped.
--
--    A 255-character prefix keeps the index selective for every field anyone
--    actually looks a value up by — names, domains, emails, phone numbers are
--    all far shorter — while making a long value indexable instead of fatal. A
--    caller matching an exact long value must still compare `value` itself; the
--    prefix narrows the scan, it does not decide the answer.
--
-- 2. golden_company gains the new fields. The view is deliberately an explicit
--    column list rather than a dynamic pivot, which means a new canonical field
--    is invisible to consumers until it is named here.

DROP INDEX IF EXISTS golden_attribute_lookup_idx;

CREATE INDEX golden_attribute_lookup_idx
    ON golden_attribute (entity_type, canonical_field, left(value, 255))
    WHERE valid_to IS NULL;

COMMENT ON INDEX golden_attribute_lookup_idx IS
    'Prefix index. Long values (technologies, seo_description) exceed the btree '
    'tuple limit if indexed whole. Match on left(value,255) to use it, then '
    'compare value for exactness.';

-- Dropped and recreated rather than replaced: CREATE OR REPLACE can only append
-- columns to a view, and these belong beside the other company facts rather
-- than trailing after the two summary counts.
DROP VIEW IF EXISTS golden_company;

CREATE VIEW golden_company AS
SELECT e.entity_id AS company_id,
    max(g.value) FILTER (WHERE g.canonical_field = 'company_name') AS company_name,
    max(g.value) FILTER (WHERE g.canonical_field = 'legal_name') AS legal_name,
    max(g.value) FILTER (WHERE g.canonical_field = 'website') AS website,
    max(g.value) FILTER (WHERE g.canonical_field = 'email') AS email,
    max(g.value) FILTER (WHERE g.canonical_field = 'phone') AS phone,
    max(g.value) FILTER (WHERE g.canonical_field = 'address_line1') AS address_line1,
    max(g.value) FILTER (WHERE g.canonical_field = 'city') AS city,
    max(g.value) FILTER (WHERE g.canonical_field = 'state_region') AS state_region,
    max(g.value) FILTER (WHERE g.canonical_field = 'postal_code') AS postal_code,
    max(g.value) FILTER (WHERE g.canonical_field = 'country') AS country,
    max(g.value) FILTER (WHERE g.canonical_field = 'industry') AS industry,
    max(g.value) FILTER (WHERE g.canonical_field = 'sic_description') AS sic_description,
    max(g.value) FILTER (WHERE g.canonical_field = 'employee_count') AS employee_count,
    max(g.value) FILTER (WHERE g.canonical_field = 'founded_year') AS founded_year,
    max(g.value) FILTER (WHERE g.canonical_field = 'linkedin_url') AS linkedin_url,
    max(g.value) FILTER (WHERE g.canonical_field = 'annual_revenue') AS annual_revenue,
    max(g.value) FILTER (WHERE g.canonical_field = 'total_funding') AS total_funding,
    max(g.value) FILTER (WHERE g.canonical_field = 'latest_funding_stage')
        AS latest_funding_stage,
    max(g.value) FILTER (WHERE g.canonical_field = 'latest_funding_amount')
        AS latest_funding_amount,
    max(g.value) FILTER (WHERE g.canonical_field = 'last_funding_date')
        AS last_funding_date,
    max(g.value) FILTER (WHERE g.canonical_field = 'retail_location_count')
        AS retail_location_count,
    max(g.value) FILTER (WHERE g.canonical_field = 'technologies') AS technologies,
    max(g.value) FILTER (WHERE g.canonical_field = 'keywords') AS keywords,
    max(g.value) FILTER (WHERE g.canonical_field = 'seo_description') AS seo_description,
    count(*) AS golden_fields,
    round(avg(g.confidence), 3) AS mean_confidence
FROM entity e
JOIN golden_attribute g ON g.entity_id = e.entity_id AND g.valid_to IS NULL
WHERE e.entity_type = 'company' AND e.status = 'active'
GROUP BY e.entity_id;
