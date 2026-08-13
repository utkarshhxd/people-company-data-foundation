-- Golden record: the single trusted value per entity per field.
--
-- Entities deliberately hold no attributes (see 0006). This is where "which of
-- the three reported names is THE name" gets answered — and, just as
-- importantly, where the answer is made auditable: which source won, which
-- rule decided it, what it beat, and how confident we are.
--
-- The golden value is DERIVED. It is never authored, and recomputing it must be
-- possible at any time from attribute_observation, which remains the only place
-- source facts live.

-- One table holds current state and history together (valid_to IS NULL means
-- current). Splitting them would let the two drift apart, and the history of a
-- trusted value is exactly the thing that must never be wrong.
CREATE TABLE golden_attribute (
    golden_id         uuid PRIMARY KEY DEFAULT uuidv7(),
    entity_id         uuid          NOT NULL REFERENCES entity (entity_id) ON DELETE CASCADE,
    entity_type       text          NOT NULL CHECK (entity_type IN ('person', 'company')),
    canonical_field   text          NOT NULL,
    -- The chosen normalized value, plus what the winning source literally
    -- wrote. Keeping the raw form here means the golden record can be explained
    -- without joining back through the observations.
    value             text          NOT NULL,
    raw_value         text,
    -- Which survivorship rule decided this, so a surprising value can always be
    -- traced to the policy that produced it rather than to a mystery.
    strategy          text          NOT NULL,
    -- Confidence IN THE CHOSEN VALUE. A fifth distinct thing from mapping
    -- confidence, validation result, match confidence and source reliability —
    -- though it is computed from the last of those.
    confidence        numeric(4, 3) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    winning_record_id uuid REFERENCES raw_record (record_id) ON DELETE SET NULL,
    winning_source_id uuid REFERENCES source (source_id),
    -- How many sources reported this exact value, and how many distinct values
    -- were in play. Two sources agreeing is a different claim from one source
    -- speaking unopposed, and both differ from a genuine conflict.
    supporting_sources integer      NOT NULL DEFAULT 1,
    competing_values  integer       NOT NULL DEFAULT 1,
    -- Every value that lost, with who said it. Nothing is discarded by being
    -- beaten.
    evidence          jsonb         NOT NULL DEFAULT '{}'::jsonb,
    valid_from        timestamptz   NOT NULL DEFAULT now(),
    valid_to          timestamptz,
    superseded_by     uuid REFERENCES golden_attribute (golden_id),
    CHECK ((valid_to IS NULL) OR (valid_to >= valid_from))
);

-- Exactly one current value per entity per field. The partial index is what
-- enforces "golden" — anything else is history.
CREATE UNIQUE INDEX golden_attribute_current_idx
    ON golden_attribute (entity_id, canonical_field)
    WHERE valid_to IS NULL;

CREATE INDEX golden_attribute_entity_idx ON golden_attribute (entity_id, canonical_field);
CREATE INDEX golden_attribute_lookup_idx
    ON golden_attribute (entity_type, canonical_field, value)
    WHERE valid_to IS NULL;

CREATE VIEW golden_record AS
SELECT entity_id, entity_type, canonical_field, value, raw_value, strategy,
       confidence, supporting_sources, competing_values, winning_source_id,
       valid_from
FROM golden_attribute
WHERE valid_to IS NULL;

-- Flat, one row per entity: the "trusted canonical data" the pipeline exists to
-- produce. Deliberately explicit rather than a dynamic pivot — a named column
-- list is what downstream consumers can actually depend on.
CREATE VIEW golden_person AS
SELECT e.entity_id AS person_id,
       max(g.value) FILTER (WHERE g.canonical_field = 'full_name')     AS full_name,
       max(g.value) FILTER (WHERE g.canonical_field = 'first_name')    AS first_name,
       max(g.value) FILTER (WHERE g.canonical_field = 'last_name')     AS last_name,
       max(g.value) FILTER (WHERE g.canonical_field = 'email')         AS email,
       max(g.value) FILTER (WHERE g.canonical_field = 'phone')         AS phone,
       max(g.value) FILTER (WHERE g.canonical_field = 'job_title')     AS job_title,
       max(g.value) FILTER (WHERE g.canonical_field = 'company_name')  AS company_name,
       max(g.value) FILTER (WHERE g.canonical_field = 'linkedin_url')  AS linkedin_url,
       max(g.value) FILTER (WHERE g.canonical_field = 'city')          AS city,
       max(g.value) FILTER (WHERE g.canonical_field = 'state_region')  AS state_region,
       max(g.value) FILTER (WHERE g.canonical_field = 'country')       AS country,
       count(*)                                                        AS golden_fields,
       round(avg(g.confidence), 3)                                     AS mean_confidence
FROM entity e
JOIN golden_attribute g ON g.entity_id = e.entity_id AND g.valid_to IS NULL
WHERE e.entity_type = 'person' AND e.status = 'active'
GROUP BY e.entity_id;

CREATE VIEW golden_company AS
SELECT e.entity_id AS company_id,
       max(g.value) FILTER (WHERE g.canonical_field = 'company_name')    AS company_name,
       max(g.value) FILTER (WHERE g.canonical_field = 'legal_name')      AS legal_name,
       max(g.value) FILTER (WHERE g.canonical_field = 'website')         AS website,
       max(g.value) FILTER (WHERE g.canonical_field = 'email')           AS email,
       max(g.value) FILTER (WHERE g.canonical_field = 'phone')           AS phone,
       max(g.value) FILTER (WHERE g.canonical_field = 'address_line1')   AS address_line1,
       max(g.value) FILTER (WHERE g.canonical_field = 'city')            AS city,
       max(g.value) FILTER (WHERE g.canonical_field = 'state_region')    AS state_region,
       max(g.value) FILTER (WHERE g.canonical_field = 'postal_code')     AS postal_code,
       max(g.value) FILTER (WHERE g.canonical_field = 'country')         AS country,
       max(g.value) FILTER (WHERE g.canonical_field = 'industry')        AS industry,
       max(g.value) FILTER (WHERE g.canonical_field = 'sic_description') AS sic_description,
       max(g.value) FILTER (WHERE g.canonical_field = 'employee_count')  AS employee_count,
       max(g.value) FILTER (WHERE g.canonical_field = 'founded_year')    AS founded_year,
       max(g.value) FILTER (WHERE g.canonical_field = 'linkedin_url')    AS linkedin_url,
       count(*)                                                          AS golden_fields,
       round(avg(g.confidence), 3)                                       AS mean_confidence
FROM entity e
JOIN golden_attribute g ON g.entity_id = e.entity_id AND g.valid_to IS NULL
WHERE e.entity_type = 'company' AND e.status = 'active'
GROUP BY e.entity_id;

-- Every value a field has ever held, newest first. This is the answer to "what
-- did we believe last month, and what changed it".
CREATE VIEW golden_history AS
SELECT entity_id, entity_type, canonical_field, value, strategy, confidence,
       winning_source_id, valid_from, valid_to,
       (valid_to IS NULL) AS is_current
FROM golden_attribute
ORDER BY entity_id, canonical_field, valid_from DESC;
