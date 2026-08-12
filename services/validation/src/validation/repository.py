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
