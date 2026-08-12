from collections.abc import Iterator
from itertools import groupby
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json


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


def observation_count(conn: psycopg.Connection, batch_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM attribute_observation WHERE batch_id = %s",
            (batch_id,),
        )
        return cur.fetchone()[0]


def iter_records(
    conn: psycopg.Connection, batch_id: str, chunk: int = 1000
) -> Iterator[tuple[Any, list[dict[str, Any]]]]:
    """Stream a batch's observations grouped into records.

    Ordering by record_id is what makes the grouping safe: the cursor never has
    to hold more than one record's attributes in memory.
    """
    name = f"observations_{str(batch_id).replace('-', '')}"
    with conn.cursor(name=name, row_factory=dict_row) as cur:
        cur.itersize = chunk
        cur.execute(
            """
            SELECT observation_id, record_id, canonical_field, source_column,
                   raw_value, normalized_value, value_type, normalization_method,
                   is_null_token
            FROM attribute_observation
            WHERE batch_id = %s
            ORDER BY record_id, source_column, value_index
            """,
            (batch_id,),
        )
        for record_id, rows in groupby(cur, key=lambda r: r["record_id"]):
            yield record_id, list(rows)


def insert_results(conn: psycopg.Connection, rows: list[tuple]) -> int:
    """Within one ruleset version the latest run wins; across versions both are
    kept, so a judgement can always be read against the rules that made it."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO validation_result (
                observation_id, record_id, batch_id, scope, canonical_field,
                rule_id, severity, outcome, message, details, ruleset_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (record_id, observation_id, rule_id, ruleset_version)
            DO UPDATE SET
                outcome      = EXCLUDED.outcome,
                severity     = EXCLUDED.severity,
                message      = EXCLUDED.message,
                details      = EXCLUDED.details,
                validated_at = now()
            """,
            rows,
        )
    return len(rows)


def upsert_record_validation(conn: psycopg.Connection, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO record_validation (
                record_id, batch_id, source_id, entity_type, status,
                error_count, warning_count, validated_attributes,
                unvalidated_attributes, failed_rules, ruleset_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (record_id) DO UPDATE SET
                status                 = EXCLUDED.status,
                error_count            = EXCLUDED.error_count,
                warning_count          = EXCLUDED.warning_count,
                validated_attributes   = EXCLUDED.validated_attributes,
                unvalidated_attributes = EXCLUDED.unvalidated_attributes,
                failed_rules           = EXCLUDED.failed_rules,
                ruleset_version        = EXCLUDED.ruleset_version,
                validated_at           = now()
            """,
            rows,
        )
    return len(rows)


def status_counts(conn: psycopg.Connection, batch_id: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                count(*)                                         AS records,
                count(*) FILTER (WHERE status = 'valid')         AS valid,
                count(*) FILTER (WHERE status = 'warning')       AS warning,
                count(*) FILTER (WHERE status = 'invalid')       AS invalid,
                coalesce(sum(validated_attributes), 0)           AS validated_attributes,
                coalesce(sum(unvalidated_attributes), 0)         AS unvalidated_attributes
            FROM record_validation WHERE batch_id = %s
            """,
            (batch_id,),
        )
        records, valid, warning, invalid, validated, unvalidated = cur.fetchone()
        return {
            "records": records,
            "valid": valid,
            "warning": warning,
            "invalid": invalid,
            "validated_attributes": validated,
            "unvalidated_attributes": unvalidated,
        }


def failure_breakdown(conn: psycopg.Connection, batch_id: str) -> list[dict[str, Any]]:
    """Which rules actually fired, most common first — the batch's real problems."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT rule_id, severity, count(*) AS failures
            FROM validation_result
            WHERE batch_id = %s AND outcome = 'fail'
            GROUP BY rule_id, severity
            ORDER BY failures DESC, rule_id
            """,
            (batch_id,),
        )
        return cur.fetchall()


def json_value(value: Any) -> Json:
    return Json(value)


# --------------------------------------------------------------------------
# quarantine
# --------------------------------------------------------------------------

def quarantine_items_for_batch(
    conn: psycopg.Connection, batch_id: str
) -> dict[Any, dict[str, Any]]:
    """Existing quarantine state for a batch's records, keyed by record_id.

    Fetched up front so routing each record is a pure decision against known
    state rather than a query per row.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT record_id, quarantine_id, status, reason_codes
            FROM quarantine_item WHERE batch_id = %s
            """,
            (batch_id,),
        )
        return {row["record_id"]: row for row in cur}


# One statement so the item and its history entry can never diverge: an
# undocumented status change is exactly the thing this table exists to prevent.
_APPLY_TRANSITION = """
WITH upserted AS (
    INSERT INTO quarantine_item (
        record_id, batch_id, source_id, entity_type, status,
        reason_codes, error_count, updated_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (record_id) DO UPDATE SET
        status       = EXCLUDED.status,
        reason_codes = EXCLUDED.reason_codes,
        error_count  = EXCLUDED.error_count,
        updated_at   = now()
    RETURNING quarantine_id, record_id
)
INSERT INTO quarantine_event (
    quarantine_id, record_id, action, from_status, to_status,
    reason_codes, actor, note
)
SELECT quarantine_id, record_id, %s, %s, %s, %s, %s, %s FROM upserted
"""


def apply_transitions(conn: psycopg.Connection, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(_APPLY_TRANSITION, rows)
    return len(rows)


def get_quarantine_item(
    conn: psycopg.Connection, record_id: str
) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT q.*, b.file_name, s.source_name
            FROM quarantine_item q
            JOIN batch b ON b.batch_id = q.batch_id
            JOIN source s ON s.source_id = q.source_id
            WHERE q.record_id = %s
            """,
            (record_id,),
        )
        return cur.fetchone()


def record_review(
    conn: psycopg.Connection,
    quarantine_id: str,
    record_id: str,
    to_status: str,
    action: str,
    from_status: str | None,
    reason_codes: list[str],
    actor: str,
    note: str | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quarantine_item
            SET status      = %s,
                reviewed_at = now(),
                reviewed_by = %s,
                review_note = %s,
                updated_at  = now()
            WHERE quarantine_id = %s
            """,
            (to_status, actor, note, quarantine_id),
        )
        cur.execute(
            """
            INSERT INTO quarantine_event (
                quarantine_id, record_id, action, from_status, to_status,
                reason_codes, actor, note
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (quarantine_id, record_id, action, from_status, to_status,
             Json(reason_codes), actor, note),
        )


def list_quarantine(
    conn: psycopg.Connection, status: str | None, entity_type: str | None, limit: int
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if status:
        clauses.append("q.status = %s")
        params.append(status)
    if entity_type:
        clauses.append("q.entity_type = %s")
        params.append(entity_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT q.record_id, q.status, q.entity_type, q.reason_codes,
                   q.error_count, q.reviewed_by, s.source_name, b.file_name,
                   r.row_number
            FROM quarantine_item q
            JOIN source s ON s.source_id = q.source_id
            JOIN batch b ON b.batch_id = q.batch_id
            JOIN raw_record r ON r.record_id = q.record_id
            {where}
            ORDER BY q.quarantined_at, r.row_number
            LIMIT %s
            """,
            params,
        )
        return cur.fetchall()


def quarantine_history(
    conn: psycopg.Connection, record_id: str
) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT action, from_status, to_status, actor, note, created_at
            FROM quarantine_event WHERE record_id = %s ORDER BY created_at
            """,
            (record_id,),
        )
        return cur.fetchall()


def record_failures(conn: psycopg.Connection, record_id: str) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT v.severity, v.rule_id, coalesce(v.canonical_field, '-') AS canonical_field,
                   coalesce(o.raw_value, '') AS raw_value, coalesce(v.message, '') AS message
            FROM validation_result v
            LEFT JOIN attribute_observation o ON o.observation_id = v.observation_id
            WHERE v.record_id = %s AND v.outcome = 'fail'
            ORDER BY (v.severity = 'error') DESC, v.rule_id
            """,
            (record_id,),
        )
        return cur.fetchall()


def quarantine_counts(conn: psycopg.Connection, batch_id: str | None) -> dict[str, int]:
    where = "WHERE batch_id = %s" if batch_id else ""
    params = (batch_id,) if batch_id else ()
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT count(*) FILTER (WHERE status = 'open')     AS open,
                   count(*) FILTER (WHERE status = 'released') AS released,
                   count(*) FILTER (WHERE status = 'rejected') AS rejected,
                   count(*) FILTER (WHERE status = 'resolved') AS resolved
            FROM quarantine_item {where}
            """,
            params,
        )
        open_, released, rejected, resolved = cur.fetchone()
        return {"open": open_, "released": released,
                "rejected": rejected, "resolved": resolved}
