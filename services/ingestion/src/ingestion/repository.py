import hashlib
import json
from collections.abc import Iterator
from typing import Any

import psycopg
from psycopg.types.json import Json

INSERT_CHUNK_SIZE = 1000


def payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_or_create_source(
    conn: psycopg.Connection, name: str, source_type: str, reliability: float
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO source (source_name, source_type, reliability)
            VALUES (%s, %s, %s)
            ON CONFLICT (source_name) DO UPDATE SET updated_at = now()
            RETURNING source_id
            """,
            (name, source_type, reliability),
        )
        return str(cur.fetchone()[0])


def find_in_flight_batch(
    conn: psycopg.Connection, source_id: str, file_hash: str
) -> str | None:
    """A batch of this exact file that is still running.

    Without this, two concurrent ingests of the same file both see no COMPLETED
    batch and both proceed, duplicating every row. The completed-batch check
    alone cannot catch it because neither has finished yet.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT batch_id FROM batch
            WHERE source_id = %s AND file_hash = %s AND status = 'running'
            ORDER BY started_at LIMIT 1
            """,
            (source_id, file_hash),
        )
        row = cur.fetchone()
        return str(row[0]) if row else None


def find_batches_from_other_sources(
    conn: psycopg.Connection, source_id: str, file_hash: str
) -> list[tuple[str, str]]:
    """The same file content already ingested under a DIFFERENT source name.

    Not blocked: two vendors genuinely can ship identical content, and each is a
    separate claim about the world that the pipeline should record separately.
    But it is far more often a typo in --source-name, so it is surfaced.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.source_name, b.batch_id
            FROM batch b
            JOIN source s ON s.source_id = b.source_id
            WHERE b.file_hash = %s AND b.source_id <> %s AND b.status = 'completed'
            ORDER BY b.started_at
            """,
            (file_hash, source_id),
        )
        return [(name, str(batch_id)) for name, batch_id in cur.fetchall()]


def find_completed_batch(conn: psycopg.Connection, source_id: str, file_hash: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT batch_id FROM batch
            WHERE source_id = %s AND file_hash = %s AND status = 'completed'
            ORDER BY started_at DESC LIMIT 1
            """,
            (source_id, file_hash),
        )
        row = cur.fetchone()
        return str(row[0]) if row else None


def create_batch(
    conn: psycopg.Connection,
    source_id: str,
    entity_type: str,
    file_path: str,
    file_name: str,
    file_hash: str,
    file_size_bytes: int,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO batch (source_id, entity_type, file_path, file_name,
                               file_hash, file_size_bytes)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING batch_id
            """,
            (source_id, entity_type, file_path, file_name, file_hash, file_size_bytes),
        )
        return str(cur.fetchone()[0])


def insert_raw_records(
    conn: psycopg.Connection,
    batch_id: str,
    source_id: str,
    entity_type: str,
    records: list[tuple[str, int, dict[str, Any]]],
) -> int:
    """Insert every record in one transaction: a file is fully ingested or not at all."""
    inserted = 0
    with conn.transaction(), conn.cursor() as cur:
        for start in range(0, len(records), INSERT_CHUNK_SIZE):
            chunk = records[start : start + INSERT_CHUNK_SIZE]
            cur.executemany(
                """
                INSERT INTO raw_record (batch_id, source_id, entity_type, source_record_id,
                                        row_number, payload_hash, raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        batch_id,
                        source_id,
                        entity_type,
                        source_record_id,
                        row_number,
                        payload_hash(payload),
                        Json(payload),
                    )
                    for source_record_id, row_number, payload in chunk
                ],
            )
            inserted += len(chunk)
    return inserted


_INSERT_RAW_RECORD = """
INSERT INTO raw_record (record_id, batch_id, source_id, entity_type,
                        source_record_id, row_number, payload_hash, raw_payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


def insert_raw_record(
    conn: psycopg.Connection,
    record_id: str,
    batch_id: str,
    source_id: str,
    entity_type: str,
    source_record_id: str,
    row_number: int,
    payload: dict[str, Any],
) -> None:
    """Insert one record with an id the caller chose.

    No transaction of its own: the record-at-a-time pipeline writes this row and
    everything derived from it inside a single transaction, so opening one here
    would split a record's write in two.
    """
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_RAW_RECORD,
            (record_id, batch_id, source_id, entity_type, source_record_id,
             row_number, payload_hash(payload), Json(payload)),
        )


def finish_batch(
    conn: psycopg.Connection, batch_id: str, rows_read: int, rows_ingested: int, rows_skipped: int
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE batch SET status = 'completed', rows_read = %s, rows_ingested = %s,
                             rows_skipped = %s, finished_at = now()
            WHERE batch_id = %s
            """,
            (rows_read, rows_ingested, rows_skipped, batch_id),
        )
    conn.commit()


def fail_batch(conn: psycopg.Connection, batch_id: str, error_message: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE batch SET status = 'failed', error_message = %s, finished_at = now()
            WHERE batch_id = %s
            """,
            (error_message[:2000], batch_id),
        )
    conn.commit()


def mark_events_published(conn: psycopg.Connection, batch_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE batch SET events_published_at = now() WHERE batch_id = %s", (batch_id,)
        )
    conn.commit()


def iter_committed_records(
    conn: psycopg.Connection, batch_id: str
) -> Iterator[tuple[str, Any]]:
    """Stream committed records so publishing can never reference an uncommitted row."""
    with conn.cursor(name=f"pub_{batch_id.replace('-', '')}") as cur:
        cur.itersize = INSERT_CHUNK_SIZE
        cur.execute(
            """
            SELECT record_id, ingested_at FROM raw_record
            WHERE batch_id = %s ORDER BY row_number
            """,
            (batch_id,),
        )
        for record_id, ingested_at in cur:
            yield str(record_id), ingested_at
