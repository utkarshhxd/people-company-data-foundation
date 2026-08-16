-- Keep the exact bytes of a payload that could not be stored.
--
-- Found by feeding the pipeline a row containing a NUL byte, which real vendor
-- exports do carry. The record failed as intended and was routed to
-- record_error -- and then the insert into record_error failed too, because
-- raw_payload is jsonb and Postgres will not accept a NUL in a text value
-- either. The safety net had exactly the same hole as the thing it was
-- catching, so the run still died on one bad row.
--
-- Two columns, because the two jobs genuinely differ:
--
--   raw_payload       jsonb, sanitized so it can always be stored, queried and
--                     read by a human reviewing the failure.
--   raw_payload_bytes bytea, the payload exactly as it arrived. bytea is the
--                     only type here that can hold any byte sequence at all,
--                     which is the point: this table exists to preserve rows
--                     the rest of the system could not, and a sanitized copy
--                     alone would quietly destroy the very character that
--                     caused the failure.
--
-- payload_sanitized says whether the two differ, so nobody has to compare them
-- to find out whether the readable copy is the whole truth.

ALTER TABLE record_error
    ADD COLUMN raw_payload_bytes bytea,
    ADD COLUMN payload_sanitized boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN record_error.raw_payload IS
    'Readable copy. Sanitized when payload_sanitized is true.';
COMMENT ON COLUMN record_error.raw_payload_bytes IS
    'The payload exactly as it arrived, JSON-encoded. Authoritative.';
