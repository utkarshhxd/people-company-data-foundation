-- Quarantine: what actually happens to a record validation called 'invalid'.
--
-- The spec's rule is that invalid records must not disappear. Quarantine is how
-- that rule is enforced without letting bad data through: the record stays
-- exactly where it is, and a row here marks it as not-yet-resolvable and names
-- the reasons. Nothing is copied and nothing is deleted.
--
-- Routing is written in the SAME transaction as the validation verdict it
-- derives from. A separate consumer would leave a window in which a record was
-- known-invalid but not yet quarantined, and if that consumer were down the
-- window would stay open indefinitely — exactly the leak this table exists to
-- prevent.

CREATE TABLE quarantine_item (
    quarantine_id  uuid PRIMARY KEY DEFAULT uuidv7(),
    -- One standing item per record. Its history lives in quarantine_event.
    record_id      uuid        NOT NULL UNIQUE REFERENCES raw_record (record_id) ON DELETE CASCADE,
    batch_id       uuid        NOT NULL REFERENCES batch (batch_id),
    source_id      uuid        NOT NULL REFERENCES source (source_id),
    entity_type    text        NOT NULL CHECK (entity_type IN ('person', 'company')),
    -- open     : awaiting a human decision
    -- released : a human judged it usable despite the failures
    -- rejected : a human confirmed it is unusable (kept forever, never resolved)
    -- resolved : re-validation passed, so the machine closed it without a human
    status         text        NOT NULL
                   CHECK (status IN ('open', 'released', 'rejected', 'resolved')),
    -- The error-severity rule ids that put it here. Kept so a reopen can tell
    -- "same problem again" from "a new problem".
    reason_codes   jsonb       NOT NULL DEFAULT '[]'::jsonb,
    error_count    integer     NOT NULL DEFAULT 0,
    quarantined_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at    timestamptz,
    reviewed_by    text,
    review_note    text,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX quarantine_item_status_idx ON quarantine_item (status);
CREATE INDEX quarantine_item_batch_idx ON quarantine_item (batch_id, status);

-- Append-only history of every disposition change, machine or human. A release
-- followed by a re-quarantine must not overwrite the fact that someone once
-- signed off on this record, and who.
CREATE TABLE quarantine_event (
    event_id      uuid PRIMARY KEY DEFAULT uuidv7(),
    quarantine_id uuid        NOT NULL REFERENCES quarantine_item (quarantine_id) ON DELETE CASCADE,
    record_id     uuid        NOT NULL REFERENCES raw_record (record_id) ON DELETE CASCADE,
    action        text        NOT NULL
                  CHECK (action IN ('quarantined', 'released', 'rejected',
                                    'resolved', 'reopened', 'reasons_changed')),
    from_status   text,
    to_status     text        NOT NULL,
    reason_codes  jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- 'system' for machine decisions, otherwise whoever ran the review CLI.
    actor         text        NOT NULL,
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX quarantine_event_record_idx ON quarantine_event (record_id, created_at);

-- The gate. Entity resolution reads this, not raw_record — which is what makes
-- quarantine an actual control rather than a passive log.
--
-- A record is resolvable if validation cleared it, or if a human explicitly
-- released it despite the failures. 'open' and 'rejected' records are absent
-- here while remaining fully present in raw_record and attribute_observation.
--
-- An explicit rejection outranks a later clean validation: if a human judged the
-- record unusable, a ruleset change must not quietly let it back in. Reopening
-- it is a human decision, which is why 'rejected' is excluded first.
CREATE VIEW resolvable_record AS
SELECT r.record_id,
       r.batch_id,
       r.source_id,
       r.entity_type,
       rv.status                     AS validation_status,
       q.status                      AS quarantine_status,
       (q.quarantine_id IS NOT NULL) AS was_quarantined
FROM raw_record r
JOIN record_validation rv ON rv.record_id = r.record_id
LEFT JOIN quarantine_item q ON q.record_id = r.record_id
WHERE (q.status IS NULL OR q.status <> 'rejected')
  AND (rv.status IN ('valid', 'warning') OR q.status = 'released');
