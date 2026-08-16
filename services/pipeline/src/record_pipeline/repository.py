"""Storage for the things only the per-record path produces."""

import json
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

# Postgres will not store a NUL in text or jsonb, and vendor exports carry them.
# A record holding one is exactly the kind of record this table is for, so the
# readable copy substitutes the standard replacement character and the exact
# bytes are kept alongside it.
_UNSTORABLE = "\x00"
_REPLACEMENT = "�"


def _sanitize(value: Any) -> tuple[Any, bool]:
    """Return a storable copy and whether anything had to change."""
    if isinstance(value, str):
        clean = value.replace(_UNSTORABLE, _REPLACEMENT)
        # An unpaired surrogate cannot be encoded as UTF-8 at all, so it would
        # fail on the way to Postgres rather than at it — the same hole migration
        # 0010 closed for NUL, one level earlier. Round-tripping through
        # surrogatepass and back with replacement is what turns it into
        # something the readable column can hold.
        try:
            clean.encode("utf-8")
        except UnicodeEncodeError:
            clean = clean.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
        return clean, clean != value
    if isinstance(value, dict):
        out, changed = {}, False
        for key, item in value.items():
            clean_key, key_changed = _sanitize(key)
            clean_item, item_changed = _sanitize(item)
            out[clean_key] = clean_item
            changed = changed or key_changed or item_changed
        return out, changed
    if isinstance(value, list):
        out_list, changed = [], False
        for item in value:
            clean, item_changed = _sanitize(item)
            out_list.append(clean)
            changed = changed or item_changed
        return out_list, changed
    return value, False


def record_failure(
    conn: psycopg.Connection,
    batch_id: str,
    source_id: str,
    entity_type: str,
    row_number: int,
    source_record_id: str | None,
    payload: dict[str, Any],
    stage: str,
    error: BaseException,
) -> None:
    """Keep a row the pipeline could not process, and why.

    Written after a rollback, so it survives the failure of the transaction that
    caused it. Nothing here may itself be rejectable: the payload is sanitized
    for the readable column and kept byte-exact in the other, and the message is
    truncated — an error too long or too strange to store would lose the record
    entirely, which is the one outcome this table exists to prevent.
    """
    readable, sanitized = _sanitize(payload)
    # surrogatepass because bytea holds any byte sequence and this column is the
    # authoritative copy: a payload that cannot be encoded is precisely the one
    # whose exact bytes matter. Refusing to encode here would lose the record.
    exact = json.dumps(payload, ensure_ascii=False).encode("utf-8", "surrogatepass")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO record_error (
                batch_id, source_id, entity_type, row_number, source_record_id,
                raw_payload, raw_payload_bytes, payload_sanitized,
                stage, error_type, error_message
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (batch_id, row_number) DO UPDATE SET
                raw_payload       = EXCLUDED.raw_payload,
                raw_payload_bytes = EXCLUDED.raw_payload_bytes,
                payload_sanitized = EXCLUDED.payload_sanitized,
                stage             = EXCLUDED.stage,
                error_type        = EXCLUDED.error_type,
                error_message     = EXCLUDED.error_message,
                status            = 'open',
                occurred_at       = now()
            """,
            (batch_id, source_id, entity_type, row_number,
             _sanitize(source_record_id)[0], Json(readable), exact, sanitized,
             stage, type(error).__name__, str(error).replace(_UNSTORABLE, _REPLACEMENT)[:4000]),
        )


def error_count(conn: psycopg.Connection, batch_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM record_error WHERE batch_id = %s AND status = 'open'",
            (batch_id,),
        )
        return cur.fetchone()[0]


def list_errors(
    conn: psycopg.Connection, batch_id: str | None, limit: int
) -> list[dict[str, Any]]:
    where = "WHERE e.status = 'open'"
    params: list[Any] = []
    if batch_id:
        where += " AND e.batch_id = %s"
        params.append(batch_id)
    params.append(limit)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT e.row_number, e.source_record_id, e.stage, e.error_type,
                   e.error_message, e.payload_sanitized, e.occurred_at,
                   s.source_name, b.file_name
            FROM record_error e
            JOIN source s ON s.source_id = e.source_id
            JOIN batch b ON b.batch_id = e.batch_id
            {where}
            ORDER BY e.occurred_at DESC, e.row_number
            LIMIT %s
            """,
            params,
        )
        return cur.fetchall()
