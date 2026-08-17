from typing import Any

import psycopg
from common.canonical import column_fingerprint  # noqa: F401  (re-exported)
from psycopg.rows import dict_row
from psycopg.types.json import Json

SAMPLE_SIZE = 200


def get_batch(conn: psycopg.Connection, batch_id: str) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT batch_id, source_id, entity_type, status, rows_ingested
            FROM batch WHERE batch_id = %s
            """,
            (batch_id,),
        )
        return cur.fetchone()


def batch_columns(conn: psycopg.Connection, batch_id: str) -> list[str]:
    """The batch's columns, in the order the reader saw them.

    This is the list the layout's fingerprint is taken over, so its order is
    load-bearing: derive it two different ways and the same file becomes two
    layouts with two independent sets of mappings, and a human's correction on
    one stops reaching the other.

    batch.columns is what the reader recorded. The fallback reads the first
    record's payload keys, which is what this did before migration 0015 and is
    wrong — raw_payload is jsonb, and jsonb reorders object keys. Batches loaded
    before 0015 have no stored order and nothing can recover it.
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


def sample_values(
    conn: psycopg.Connection, batch_id: str, columns: list[str]
) -> dict[str, list[str]]:
    samples: dict[str, list[str]] = {column: [] for column in columns}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT raw_payload FROM raw_record
            WHERE batch_id = %s ORDER BY row_number LIMIT %s
            """,
            (batch_id, SAMPLE_SIZE),
        )
        for (payload,) in cur:
            for column in columns:
                value = payload.get(column)
                if value is not None:
                    samples[column].append(str(value))
    return samples


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


def create_source_schema(
    conn: psycopg.Connection,
    source_id: str,
    batch_id: str,
    entity_type: str,
    fingerprint: str,
    columns: list[str],
    schema_version: str,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO source_schema (source_id, first_seen_batch_id, entity_type,
                                       column_fingerprint, columns, schema_version)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING source_schema_id
            """,
            (source_id, batch_id, entity_type, fingerprint, Json(columns), schema_version),
        )
        return str(cur.fetchone()[0])


def insert_mappings(conn: psycopg.Connection, source_schema_id: str, mappings) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO column_mapping (source_schema_id, source_column, canonical_field,
                                        mapping_method, mapping_confidence, mapping_status,
                                        evidence, schema_version, subject)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_schema_id, source_column) DO NOTHING
            """,
            [
                (
                    source_schema_id,
                    m.source_column,
                    m.canonical_field,
                    m.method,
                    m.confidence,
                    m.status,
                    Json(m.evidence),
                    m.schema_version,
                    m.subject,
                )
                for m in mappings
            ],
        )
    return len(mappings)


def list_mappings(
    conn: psycopg.Connection, source_schema_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if source_schema_id:
        clauses.append("cm.source_schema_id = %s")
        params.append(source_schema_id)
    if status:
        clauses.append("cm.mapping_status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT cm.mapping_id, cm.source_column, cm.canonical_field, cm.mapping_method,
                   cm.mapping_confidence, cm.mapping_status, cm.evidence,
                   s.source_name, ss.entity_type
            FROM column_mapping cm
            JOIN source_schema ss USING (source_schema_id)
            JOIN source s ON s.source_id = ss.source_id
            {where}
            ORDER BY s.source_name, cm.source_column
            """,
            params,
        )
        return cur.fetchall()


def set_mapping(
    conn: psycopg.Connection,
    mapping_id: str,
    canonical_field: str | None,
    status: str,
    reviewed_by: str,
) -> bool:
    """Record a human decision. Method becomes 'manual' with full confidence."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE column_mapping
            SET canonical_field = %s, mapping_status = %s, mapping_method = 'manual',
                mapping_confidence = 1.0, reviewed_at = now(), reviewed_by = %s,
                evidence = evidence || jsonb_build_object('manual_override', true)
            WHERE mapping_id = %s
            """,
            (canonical_field, status, reviewed_by, mapping_id),
        )
        return cur.rowcount > 0


def mapping_counts(conn: psycopg.Connection, source_schema_id: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT mapping_status, count(*) FROM column_mapping
            WHERE source_schema_id = %s GROUP BY mapping_status
            """,
            (source_schema_id,),
        )
        return {status: count for status, count in cur}


def find_source_schema_by_columns(
    conn: psycopg.Connection, source_id: str, columns: list[str], schema_version: str
) -> str | None:
    """Find a layout by the set of column names it holds, ignoring their order.

    The fingerprint is the normal way in, and it is order-sensitive on purpose:
    two files with the same names in a different order are different layouts and
    map differently. This exists for the one case where the order is not
    recoverable — batches loaded before migration 0015, whose column list can
    only be read back from jsonb keys, which jsonb reordered.

    Matching on the set is enough to identify the layout in that situation and
    not enough to be trusted generally, which is why it is a fallback and not
    the lookup.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_schema_id FROM source_schema
            WHERE source_id = %s AND schema_version = %s
              AND (SELECT array_agg(value ORDER BY value)
                     FROM jsonb_array_elements_text(columns))
                  = (SELECT array_agg(name ORDER BY name)
                       FROM unnest(%s::text[]) AS name)
            ORDER BY created_at
            LIMIT 1
            """,
            (source_id, schema_version, columns),
        )
        row = cur.fetchone()
        return str(row[0]) if row else None
