from typing import Any

import psycopg
from psycopg.rows import dict_row

# Only a confirmed mapping is allowed to set canonical_field. An unreviewed
# guess still produces an observation — it just isn't labelled as canonical.
ACCEPTED_MAPPING_STATUSES = ("auto_accepted", "approved")


def get_batch(conn: psycopg.Connection, batch_id: str) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT batch_id, source_id, entity_type, status
            FROM batch WHERE batch_id = %s
            """,
            (batch_id,),
        )
        return cur.fetchone()


def get_column_mappings(
    conn: psycopg.Connection, source_schema_id: str
) -> dict[str, dict[str, Any]]:
    """source_column -> mapping decision, for every column of the layout."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT source_column, canonical_field, mapping_status, subject
            FROM column_mapping WHERE source_schema_id = %s
            """,
            (source_schema_id,),
        )
        return {row["source_column"]: row for row in cur}


def batch_columns(conn: psycopg.Connection, batch_id: str) -> list[str]:
    """The batch's columns, in the order the reader saw them.

    Read from batch.columns, which whoever read the file recorded. The fallback
    below reconstructs the list from the first record's payload keys, which is
    what this did before migration 0015 -- and it is only a fallback because
    raw_payload is jsonb, and jsonb reorders object keys. Batches loaded before
    0015 have no stored order and get the old, wrong-ordered answer; nothing can
    recover what was never written down.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT columns FROM batch WHERE batch_id = %s", (batch_id,))
        row = cur.fetchone()
        if row and row[0]:
            return list(row[0])

        cur.execute(
            """
            SELECT raw_payload FROM raw_record
            WHERE batch_id = %s ORDER BY row_number LIMIT 1
            """,
            (batch_id,),
        )
        row = cur.fetchone()
        return list(row[0].keys()) if row else []


def find_source_schema(
    conn: psycopg.Connection, source_id: str, fingerprint: str, schema_version: str
) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_schema_id FROM source_schema
            WHERE source_id = %s AND column_fingerprint = %s AND schema_version = %s
            """,
            (source_id, fingerprint, schema_version),
        )
        row = cur.fetchone()
        return str(row[0]) if row else None


def iter_records(conn: psycopg.Connection, batch_id: str, chunk: int = 1000):
    """Stream a batch's raw records with a server-side cursor."""
    with conn.cursor(name=f"records_{batch_id.replace('-', '')}") as cur:
        cur.itersize = chunk
        cur.execute(
            """
            SELECT record_id, raw_payload, ingested_at
            FROM raw_record WHERE batch_id = %s ORDER BY row_number
            """,
            (batch_id,),
        )
        yield from cur


def insert_observations(conn: psycopg.Connection, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO attribute_observation (
                record_id, batch_id, source_id, entity_type, source_column,
                canonical_field, mapping_status, value_index, raw_value,
                normalized_value, value_type, normalization_method,
                is_null_token, observed_at, subject
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            -- raw_value and observed_at are deliberately NOT updated: they are
            -- what the source said, and that never changes. Everything else here
            -- is derived, and must be refreshable — otherwise approving a
            -- mapping would never reach batches that were already normalized,
            -- and human review would be pointless for anything but new files.
            -- subject is derived too: a reviewer correcting 'Company City' is
            -- correcting whose attribute it is as much as which field.
            ON CONFLICT (record_id, source_column, value_index) DO UPDATE SET
                canonical_field      = EXCLUDED.canonical_field,
                mapping_status       = EXCLUDED.mapping_status,
                normalized_value     = EXCLUDED.normalized_value,
                value_type           = EXCLUDED.value_type,
                normalization_method = EXCLUDED.normalization_method,
                is_null_token        = EXCLUDED.is_null_token,
                subject              = EXCLUDED.subject
            """,
            rows,
        )
    return len(rows)


def insert_observations_with_ids(conn: psycopg.Connection, rows: list[tuple]) -> int:
    """Same insert, but the caller supplies observation_id as the first column.

    Exists because validation results reference observations. When the ids come
    from the server, a record's validation cannot be written until its
    observations have come back — so the record-at-a-time pipeline chooses the
    ids itself and sends both together. The conflict clause is deliberately
    identical to insert_observations: raw_value and observed_at are what the
    source said and are never updated; everything derived is refreshable.
    """
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO attribute_observation (
                observation_id, record_id, batch_id, source_id, entity_type,
                source_column, canonical_field, mapping_status, value_index,
                raw_value, normalized_value, value_type, normalization_method,
                is_null_token, observed_at, subject
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (record_id, source_column, value_index) DO UPDATE SET
                canonical_field      = EXCLUDED.canonical_field,
                mapping_status       = EXCLUDED.mapping_status,
                normalized_value     = EXCLUDED.normalized_value,
                value_type           = EXCLUDED.value_type,
                normalization_method = EXCLUDED.normalization_method,
                is_null_token        = EXCLUDED.is_null_token,
                subject              = EXCLUDED.subject
            """,
            rows,
        )
    return len(rows)


def observation_counts(conn: psycopg.Connection, batch_id: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE canonical_field IS NOT NULL) AS canonical,
                count(*) FILTER (WHERE canonical_field IS NULL) AS uncanonical,
                count(*) FILTER (WHERE is_null_token) AS null_tokens,
                count(*) FILTER (WHERE value_index > 0) AS extra_values
            FROM attribute_observation WHERE batch_id = %s
            """,
            (batch_id,),
        )
        total, canonical, uncanonical, null_tokens, extra = cur.fetchone()
        return {
            "total": total,
            "canonical": canonical,
            "uncanonical": uncanonical,
            "null_tokens": null_tokens,
            "extra_values": extra,
        }
