-- A stage can be stopped, and stopping it survives a restart.
--
-- The failure this exists for: something changes -- a vendor's export format,
-- a rule, a mapping -- and suddenly every record fails validation. Nothing is
-- lost when that happens; each record is quarantined with its reasons, exactly
-- as designed. But quarantining ten million records one at a time is not a
-- useful thing to have done, and the moment worth catching it is record 500,
-- not the next morning. The pipeline should stop and ask.
--
-- State lives here rather than in a process because every consumer restarts,
-- and a pause that a restart clears is not a pause. It is per stage, not per
-- batch: if validation is broken it is broken for everything, and pausing one
-- batch would leave the next file walking into the same wall.

CREATE TABLE pipeline_control (
    stage      text PRIMARY KEY
               CHECK (stage IN ('mapping', 'normalization', 'validation',
                                'resolution', 'golden', 'ingestion')),
    state      text        NOT NULL DEFAULT 'running'
               CHECK (state IN ('running', 'paused')),
    -- Why, in a sentence somebody woken up at 3am can act on.
    reason     text,
    -- 'auto' when the stage stopped itself, otherwise who typed the command.
    -- Worth distinguishing: an automatic stop is evidence about the data, and
    -- a manual one is evidence about what a person was doing.
    changed_by text        NOT NULL DEFAULT 'auto',
    -- What the stage saw when it stopped -- counts, and the rules that failed
    -- most. This is the whole point of stopping rather than carrying on: the
    -- investigation should not have to start from nothing.
    evidence   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    changed_at timestamptz NOT NULL DEFAULT now()
);

-- The history, because "who stopped this, and why, and who decided it was
-- fine" is exactly the question asked afterwards. Same shape as
-- quarantine_event: the standing state above, its story here.
CREATE TABLE pipeline_control_event (
    event_id   uuid PRIMARY KEY DEFAULT uuidv7(),
    stage      text        NOT NULL,
    from_state text        NOT NULL,
    to_state   text        NOT NULL,
    reason     text,
    changed_by text        NOT NULL,
    evidence   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    note       text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX pipeline_control_event_stage_idx
    ON pipeline_control_event (stage, created_at DESC);
