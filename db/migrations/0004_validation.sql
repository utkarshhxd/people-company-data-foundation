-- Validation: assert whether an observed value is usable AS the canonical field
-- it was mapped to. Validation NEVER repairs a value and never deletes a record.
-- It attaches judgements alongside the observation, which stays untouched.
--
-- Validation result is its own dimension. It must never be conflated with:
--   * mapping confidence  (how sure we are the COLUMN means this field)
--   * source reliability  (how much we trust this VENDOR in general)
--   * match confidence    (how sure we are two records are the same ENTITY)
-- A perfectly-mapped column from a trusted vendor can still hold a broken email.

CREATE TABLE validation_result (
    validation_id   uuid PRIMARY KEY DEFAULT uuidv7(),
    -- NULL for record-scoped rules, which judge the row as a whole rather than
    -- any single value.
    observation_id  uuid REFERENCES attribute_observation (observation_id) ON DELETE CASCADE,
    record_id       uuid        NOT NULL REFERENCES raw_record (record_id) ON DELETE CASCADE,
    batch_id        uuid        NOT NULL REFERENCES batch (batch_id),
    scope           text        NOT NULL CHECK (scope IN ('attribute', 'record')),
    canonical_field text,
    rule_id         text        NOT NULL,
    severity        text        NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    -- Passes are stored, not just failures. Without them, "no row" would be
    -- ambiguous between "the rule passed" and "the rule never ran" — and the
    -- second is common here, because unmapped columns have no rules to run.
    outcome         text        NOT NULL CHECK (outcome IN ('pass', 'fail')),
    message         text,
    details         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- Stamped like the canonical schema version: a judgement is only meaningful
    -- relative to the rules that produced it. A new ruleset writes new rows
    -- rather than silently rewriting history.
    ruleset_version text        NOT NULL,
    validated_at    timestamptz NOT NULL DEFAULT now(),
    -- NULLS NOT DISTINCT so record-scoped rules (observation_id IS NULL) are
    -- deduplicated per record too. Re-running the same ruleset is a no-op.
    UNIQUE NULLS NOT DISTINCT (record_id, observation_id, rule_id, ruleset_version)
);

CREATE INDEX validation_result_batch_idx ON validation_result (batch_id);
CREATE INDEX validation_result_failures_idx
    ON validation_result (batch_id, severity, rule_id)
    WHERE outcome = 'fail';

-- Current validation state of a record. One row per record: this is "where the
-- record stands now", while validation_result holds how it got there.
CREATE TABLE record_validation (
    record_id              uuid PRIMARY KEY REFERENCES raw_record (record_id) ON DELETE CASCADE,
    batch_id               uuid        NOT NULL REFERENCES batch (batch_id),
    source_id              uuid        NOT NULL REFERENCES source (source_id),
    entity_type            text        NOT NULL CHECK (entity_type IN ('person', 'company')),
    -- 'invalid' means "not safe to resolve to an entity yet" — NOT "delete me".
    -- The raw record and all its observations remain; quarantine (next
    -- increment) routes these for review instead of dropping them.
    status                 text        NOT NULL CHECK (status IN ('valid', 'warning', 'invalid')),
    error_count            integer     NOT NULL DEFAULT 0,
    warning_count          integer     NOT NULL DEFAULT 0,
    -- Makes the coverage gap countable: attributes with no confirmed canonical
    -- field have no rules to run, and a record that is 90% unvalidated is not
    -- the same as a record that passed everything.
    validated_attributes   integer     NOT NULL DEFAULT 0,
    unvalidated_attributes integer     NOT NULL DEFAULT 0,
    failed_rules           jsonb       NOT NULL DEFAULT '[]'::jsonb,
    ruleset_version        text        NOT NULL,
    validated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX record_validation_batch_status_idx ON record_validation (batch_id, status);
CREATE INDEX record_validation_status_idx ON record_validation (status)
    WHERE status <> 'valid';
