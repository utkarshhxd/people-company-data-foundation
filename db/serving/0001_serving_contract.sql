-- The serving contract: what the display app's schema must look like for the
-- projection to be able to write into it.
--
-- Run this against the SERVING database (the app that displays leads), not
-- against the pipeline database. It is the only file in this repository that
-- touches a schema this repository does not own, which is why it lives apart
-- from db/migrations/ and is applied by hand rather than by the `migrate` job.
--
--     psql "$SERVING_URL" -v ON_ERROR_STOP=1 -f db/serving/0001_serving_contract.sql
--
-- It does four things:
--
--   1. Gives `leads` and `companies` a stable identity that survives a
--      re-projection, so the job is an upsert rather than an ever-growing pile
--      of near-duplicates.
--   2. Turns the columns the app filters on from text into the types those
--      filters need. `WHERE employees_count > 500` cannot work against a
--      VARCHAR, and no index makes it work.
--   3. Makes deduplication possible at all by putting unique constraints on the
--      two natural keys, which the schema had indexed but not constrained.
--   4. Removes `leads_staging`, whose job -- hold rows, validate them, note why
--      they failed -- is the pipeline's job, done here with provenance.
--
-- BEFORE RUNNING: take a backup. Step 2 rewrites text columns in place, and a
-- value that does not parse as a number becomes NULL. Every original string is
-- copied into `serving_pre_numeric_backup` first, so nothing is destroyed by
-- this file, but that table is a safety net rather than a backup.

BEGIN;

-- ---------------------------------------------------------------------------
-- 0. Refuse to run if the natural keys are not yet unique
-- ---------------------------------------------------------------------------
-- Adding a unique index over data that already has duplicates fails with a
-- message naming one row, which tells an operator nothing about the scale of
-- what they have to clean up. Say it plainly instead, before anything changed.

DO $check$
DECLARE
    duplicate_domains bigint;
    duplicate_emails  bigint;
BEGIN
    SELECT count(*) INTO duplicate_domains FROM (
        SELECT lower(domain) FROM companies
        WHERE domain IS NOT NULL AND btrim(domain) <> ''
        GROUP BY 1 HAVING count(*) > 1
    ) d;

    SELECT count(*) INTO duplicate_emails FROM (
        SELECT lower(email_address) FROM leads
        WHERE email_address IS NOT NULL AND btrim(email_address) <> ''
        GROUP BY 1 HAVING count(*) > 1
    ) e;

    IF duplicate_domains > 0 OR duplicate_emails > 0 THEN
        RAISE EXCEPTION
            'cannot apply the serving contract: % duplicated company domain(s) and % duplicated lead email(s) already exist. Resolve them first -- see db/serving/README.md.',
            duplicate_domains, duplicate_emails;
    END IF;
END
$check$;

-- ---------------------------------------------------------------------------
-- 1. Stable identity
-- ---------------------------------------------------------------------------
-- `entity_id` is the pipeline's entity_id: the thing entity resolution decided
-- is one real person or one real company. It is added alongside the existing
-- primary key rather than replacing it, because `lead_id` is referenced by
-- list_leads, audit_ledger and data_flags, and rewriting a key three tables
-- point at is a migration in its own right with no benefit here.
--
-- Nullable on purpose: rows that predate the pipeline keep existing, they are
-- simply not projected onto. A NULL here means "this row has no upstream".

ALTER TABLE companies ADD COLUMN IF NOT EXISTS entity_id uuid;
ALTER TABLE leads     ADD COLUMN IF NOT EXISTS entity_id uuid;

CREATE UNIQUE INDEX IF NOT EXISTS companies_entity_id_key ON companies (entity_id);
CREATE UNIQUE INDEX IF NOT EXISTS leads_entity_id_key     ON leads (entity_id);

COMMENT ON COLUMN companies.entity_id IS
    'The pipeline entity this row projects from. NULL means no upstream record.';
COMMENT ON COLUMN leads.entity_id IS
    'The pipeline entity this row projects from. NULL means no upstream record.';

-- ---------------------------------------------------------------------------
-- 2. Types the filters need
-- ---------------------------------------------------------------------------
-- Every one of these arrives from the pipeline already normalized: a headcount
-- is digits, a revenue is whole units, a funding date is ISO. What the source
-- literally wrote is kept upstream in attribute_observation.raw_value, so
-- narrowing the type here loses nothing that is not still recorded elsewhere.

CREATE TABLE IF NOT EXISTS serving_pre_numeric_backup AS
SELECT c.company_id,
       c.employees_count        AS employees_count_text,
       c.founded_year           AS founded_year_text,
       c.linkedin_followers     AS linkedin_followers_text,
       c.annual_revenue         AS annual_revenue_text,
       c.total_funding          AS total_funding_text,
       c.latest_funding_amount  AS latest_funding_amount_text,
       c.latest_funding_date    AS latest_funding_date_text
FROM companies c;

COMMENT ON TABLE serving_pre_numeric_backup IS
    'Every company text value as it stood before the serving contract narrowed these columns to numeric and date types. Safe to drop once the projection has run and the values have been checked.';

CREATE TABLE IF NOT EXISTS serving_pre_numeric_backup_leads AS
SELECT lead_id, confidence_score AS confidence_score_text FROM leads;

-- A cast that returns NULL rather than aborting the migration on the one row
-- someone typed 'unknown' into. Only used by this file, dropped at the end.
CREATE OR REPLACE FUNCTION serving_to_numeric(value text)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE AS $fn$
BEGIN
    RETURN nullif(btrim(value), '')::numeric;
EXCEPTION WHEN others THEN
    RETURN NULL;
END
$fn$;

CREATE OR REPLACE FUNCTION serving_to_date(value text)
RETURNS date LANGUAGE plpgsql IMMUTABLE AS $fn$
BEGIN
    RETURN nullif(btrim(value), '')::date;
EXCEPTION WHEN others THEN
    RETURN NULL;
END
$fn$;

ALTER TABLE companies
    ALTER COLUMN employees_count       TYPE integer
        USING serving_to_numeric(employees_count)::integer,
    ALTER COLUMN founded_year          TYPE smallint
        USING serving_to_numeric(founded_year)::smallint,
    ALTER COLUMN linkedin_followers    TYPE integer
        USING serving_to_numeric(linkedin_followers)::integer,
    ALTER COLUMN annual_revenue        TYPE numeric(20, 2)
        USING serving_to_numeric(annual_revenue),
    ALTER COLUMN total_funding         TYPE numeric(20, 2)
        USING serving_to_numeric(total_funding),
    ALTER COLUMN latest_funding_amount TYPE numeric(20, 2)
        USING serving_to_numeric(latest_funding_amount),
    ALTER COLUMN latest_funding_date   TYPE date
        USING serving_to_date(latest_funding_date);

-- Confidence is a fraction between 0 and 1 upstream and should compare as one.
ALTER TABLE leads
    ALTER COLUMN confidence_score TYPE numeric(4, 3)
        USING serving_to_numeric(confidence_score);

ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_confidence_score_range;
ALTER TABLE leads ADD CONSTRAINT leads_confidence_score_range
    CHECK (confidence_score IS NULL
           OR (confidence_score >= 0 AND confidence_score <= 1));

DROP FUNCTION serving_to_numeric(text);
DROP FUNCTION serving_to_date(text);

-- The filters those types exist for.
CREATE INDEX IF NOT EXISTS idx_companies_employees_count
    ON companies (employees_count) WHERE employees_count IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_companies_founded_year
    ON companies (founded_year) WHERE founded_year IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_companies_annual_revenue
    ON companies (annual_revenue) WHERE annual_revenue IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. The natural keys, actually enforced
-- ---------------------------------------------------------------------------
-- `idx_companies_domain` and the lead indexes made these lookups fast without
-- making them unique, so nothing stopped the same company arriving twice. Case
-- and blank-versus-NULL are folded, because 'Acme.com', 'acme.com' and a stray
-- empty string are one company and no companies.

UPDATE companies SET domain = NULL WHERE btrim(domain) = '';
UPDATE leads SET email_address = NULL WHERE btrim(email_address) = '';

CREATE UNIQUE INDEX IF NOT EXISTS companies_domain_key
    ON companies (lower(domain)) WHERE domain IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS leads_email_address_key
    ON leads (lower(email_address)) WHERE email_address IS NOT NULL;

-- Deleting a company currently errors instead of doing anything, because the
-- foreign key was declared with the default NO ACTION and no one chose it.
-- A lead whose company is gone is still a lead.
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_company_id_fkey;
ALTER TABLE leads ADD CONSTRAINT leads_company_id_fkey
    FOREIGN KEY (company_id) REFERENCES companies (company_id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- 4. Billing and audit reads
-- ---------------------------------------------------------------------------
-- The composite index on audit_ledger is (user_id, action_type, lead_id), which
-- serves "did this user reveal this lead". The question actually asked every
-- billing cycle is "what did this user spend between two dates", and that index
-- cannot answer it without scanning every row the user ever produced.

CREATE INDEX IF NOT EXISTS idx_audit_ledger_user_created
    ON audit_ledger (user_id, created_at DESC);

-- users.credit_balance and sum(user_credit_batches.amount_remaining) are two
-- places the same number lives, and they will disagree. The batches are the
-- ledger -- they carry expiry, which a single total cannot represent -- so this
-- view is the truth and the column is a cache of it.
--
-- Dropping users.credit_balance is the right end state and is deliberately NOT
-- done here: application code reads it, and breaking that is a change for the
-- application repository to make. Reconcile with:
--     SELECT * FROM user_credit_balance WHERE abs(cached - available) > 0.005;
CREATE OR REPLACE VIEW user_credit_balance AS
SELECT u.user_id,
       u.credit_balance AS cached,
       COALESCE(sum(b.amount_remaining) FILTER (
           WHERE b.expires_at IS NULL OR b.expires_at > now()
       ), 0)::numeric(10, 2) AS available
FROM users u
LEFT JOIN user_credit_batches b ON b.user_id = u.user_id
GROUP BY u.user_id, u.credit_balance;

-- ---------------------------------------------------------------------------
-- 5. updated_at that actually updates
-- ---------------------------------------------------------------------------
-- DEFAULT CURRENT_TIMESTAMP sets the column once, at insert. Every one of these
-- columns has therefore been recording created_at under another name.

CREATE OR REPLACE FUNCTION serving_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS companies_touch_updated_at ON companies;
CREATE TRIGGER companies_touch_updated_at BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION serving_touch_updated_at();

DROP TRIGGER IF EXISTS leads_touch_updated_at ON leads;
CREATE TRIGGER leads_touch_updated_at BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION serving_touch_updated_at();

DROP TRIGGER IF EXISTS subscriptions_touch_updated_at ON subscriptions;
CREATE TRIGGER subscriptions_touch_updated_at BEFORE UPDATE ON subscriptions
    FOR EACH ROW EXECUTE FUNCTION serving_touch_updated_at();

-- ---------------------------------------------------------------------------
-- 6. Where the projection left off
-- ---------------------------------------------------------------------------
-- One row per entity type. The watermark lives in the serving database rather
-- than in the pipeline's, because it describes the state of THIS copy: restore
-- the serving database from an older dump and the watermark comes back with it,
-- which is exactly the behaviour that makes the next run correct.

CREATE TABLE IF NOT EXISTS projection_watermark (
    entity_type  text PRIMARY KEY CHECK (entity_type IN ('person', 'company')),
    projected_to timestamptz NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 7. leads_staging goes
-- ---------------------------------------------------------------------------
-- It holds unvalidated rows, a validation_status and an error_notes string --
-- which is raw_record, record_validation and quarantine_item from the pipeline,
-- flattened into one table that cannot say which source said what, cannot hold
-- two sources disagreeing, and loses the original value on promotion.
--
-- Two staging paths into one serving table is the failure this whole exercise
-- exists to avoid, so there is one, and it is the one with provenance.

DROP TABLE IF EXISTS leads_staging;

COMMIT;
