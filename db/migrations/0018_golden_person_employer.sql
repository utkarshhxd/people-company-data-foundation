-- golden_person.company_name was always NULL, and structurally had to be.
--
-- The view read `company_name` from the person's own golden attributes. It is
-- never there. A person row's employer columns are captured with
-- subject = 'employer', and the person entity's golden record is built only
-- from observations whose subject matches the link's role ('self') -- which is
-- correct and deliberate: without that filter the employer's city, phone and
-- postcode would compete to become the person's own.
--
-- So the employer's name lives on the COMPANY entity, and the person is joined
-- to it by an `employed_at` relationship (ADR 0013). The view simply was not
-- reading that relationship, and presented the result as "no company name" --
-- a column that looked like missing data and was actually a missing join.
--
-- Fixed with correlated subqueries rather than a join, because joining the
-- relationship would multiply the rows feeding `golden_fields` and
-- `mean_confidence`, quietly corrupting two columns to fix one.
--
-- `employer_id` is exposed alongside the name so a consumer can reach the
-- employer's full golden record instead of matching on a string.

DROP VIEW IF EXISTS golden_person;

CREATE VIEW golden_person AS
SELECT e.entity_id AS person_id,
    max(g.value) FILTER (WHERE g.canonical_field = 'full_name')     AS full_name,
    max(g.value) FILTER (WHERE g.canonical_field = 'first_name')    AS first_name,
    max(g.value) FILTER (WHERE g.canonical_field = 'last_name')     AS last_name,
    max(g.value) FILTER (WHERE g.canonical_field = 'email')         AS email,
    max(g.value) FILTER (WHERE g.canonical_field = 'phone')         AS phone,
    max(g.value) FILTER (WHERE g.canonical_field = 'job_title')     AS job_title,
    max(g.value) FILTER (WHERE g.canonical_field = 'seniority')     AS seniority,
    max(g.value) FILTER (WHERE g.canonical_field = 'department')    AS department,
    max(g.value) FILTER (WHERE g.canonical_field = 'linkedin_url')  AS linkedin_url,
    max(g.value) FILTER (WHERE g.canonical_field = 'city')          AS city,
    max(g.value) FILTER (WHERE g.canonical_field = 'state_region')  AS state_region,
    max(g.value) FILTER (WHERE g.canonical_field = 'country')       AS country,

    -- The employer, reached through the relationship rather than through the
    -- person's own attributes. Ordered by confidence so that a person linked to
    -- more than one employer resolves to the best-supported name rather than an
    -- arbitrary one; ties break on the value so the view is deterministic.
    (SELECT r.to_entity_id
       FROM entity_relationship r
      WHERE r.from_entity_id = e.entity_id
        AND r.relationship_type = 'employed_at'
        AND r.valid_to IS NULL
      ORDER BY r.valid_from DESC, r.to_entity_id
      LIMIT 1)                                                      AS employer_id,

    (SELECT ga.value
       FROM entity_relationship r
       JOIN golden_attribute ga
         ON ga.entity_id = r.to_entity_id
        AND ga.canonical_field = 'company_name'
        AND ga.valid_to IS NULL
      WHERE r.from_entity_id = e.entity_id
        AND r.relationship_type = 'employed_at'
        AND r.valid_to IS NULL
      ORDER BY ga.confidence DESC, ga.value
      LIMIT 1)                                                      AS company_name,

    (SELECT ga.value
       FROM entity_relationship r
       JOIN golden_attribute ga
         ON ga.entity_id = r.to_entity_id
        AND ga.canonical_field = 'website'
        AND ga.valid_to IS NULL
      WHERE r.from_entity_id = e.entity_id
        AND r.relationship_type = 'employed_at'
        AND r.valid_to IS NULL
      ORDER BY ga.confidence DESC, ga.value
      LIMIT 1)                                                      AS company_website,

    -- Counts the PERSON's own fields. The employer's name is the employer's
    -- fact, and counting it here would overstate how much we know about the
    -- person.
    count(*)                                                        AS golden_fields,
    round(avg(g.confidence), 3)                                     AS mean_confidence
FROM entity e
JOIN golden_attribute g ON g.entity_id = e.entity_id AND g.valid_to IS NULL
WHERE e.entity_type = 'person' AND e.status = 'active'
GROUP BY e.entity_id;

COMMENT ON VIEW golden_person IS
    'One row per person. company_name/company_website come from the employed_at '
    'relationship, not from the person''s own attributes -- a person row''s '
    'employer columns belong to the company entity.';
