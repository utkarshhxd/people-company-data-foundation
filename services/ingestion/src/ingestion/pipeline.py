import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from common import events
from common.db import connect
from common.kafka import EventProducer, ensure_topics

from ingestion import repository
from ingestion.readers import read_file

logger = logging.getLogger(__name__)


class ReingestBlocked(Exception):
    pass


@dataclass
class IngestResult:
    batch_id: str
    source_id: str
    rows_read: int
    rows_ingested: int
    rows_skipped: int
    events_published: bool


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_record_ids(
    rows: list[dict[str, str | None]], record_id_column: str | None
) -> list[str]:
    if record_id_column is None:
        return [str(index + 1) for index in range(len(rows))]
    if rows and record_id_column not in rows[0]:
        raise KeyError(f"--record-id-column {record_id_column!r} is not a column in this file")
    return [(row.get(record_id_column) or str(index + 1)) for index, row in enumerate(rows)]


def ingest(
    path: Path,
    entity_type: str,
    source_name: str,
    source_type: str,
    reliability: float,
    record_id_column: str | None = None,
    allow_reingest: bool = False,
) -> IngestResult:
    digest = file_hash(path)
    size = path.stat().st_size

    with connect() as conn:
        source_id = repository.get_or_create_source(conn, source_name, source_type, reliability)
        conn.commit()

        previous = repository.find_completed_batch(conn, source_id, digest)
        if previous and not allow_reingest:
            raise ReingestBlocked(
                f"{path.name} was already ingested for source {source_name!r} "
                f"as batch {previous}. Re-run with --allow-reingest to ingest it again."
            )

        batch_id = repository.create_batch(
            conn, source_id, entity_type, str(path), path.name, digest, size
        )
        conn.commit()
        logger.info("batch %s started for %s", batch_id, path.name)

        try:
            _, rows = read_file(path)
            source_record_ids = _source_record_ids(rows, record_id_column)
            records = [
                (source_record_ids[index], index + 1, row) for index, row in enumerate(rows)
            ]
            rows_ingested = repository.insert_raw_records(
                conn, batch_id, source_id, entity_type, records
            )
        except Exception as exc:
            conn.rollback()
            repository.fail_batch(conn, batch_id, f"{type(exc).__name__}: {exc}")
            logger.error("batch %s failed: %s", batch_id, exc)
            raise

        repository.finish_batch(conn, batch_id, len(rows), rows_ingested, 0)
        logger.info("batch %s committed %d record(s)", batch_id, rows_ingested)

        # Only now, with rows committed, publish references to them. Publishing
        # reads back committed rows so an event can never point at a row that
        # doesn't exist.
        published = _publish(conn, batch_id, source_id, entity_type, path.name, rows_ingested)

    return IngestResult(
        batch_id=batch_id,
        source_id=source_id,
        rows_read=len(rows),
        rows_ingested=rows_ingested,
        rows_skipped=0,
        events_published=published,
    )


def _publish(
    conn, batch_id: str, source_id: str, entity_type: str, file_name: str, rows_ingested: int
) -> bool:
    try:
        ensure_topics()
        producer = EventProducer()
        for record_id, ingested_at in repository.iter_committed_records(conn, batch_id):
            producer.publish(
                events.TOPIC_RAW_RECORD_INGESTED,
                key=record_id,
                event=events.raw_record_ingested(
                    record_id, batch_id, source_id, entity_type, ingested_at
                ),
            )
        producer.publish(
            events.TOPIC_BATCH_INGESTED,
            key=batch_id,
            event=events.batch_ingested(
                batch_id,
                source_id,
                entity_type,
                file_name,
                rows_ingested,
                datetime.now(timezone.utc),
            ),
        )
        producer.flush()
    except Exception as exc:
        # Rows are committed and the batch is 'completed'. Leaving
        # events_published_at NULL makes the gap visible and replayable rather
        # than failing the whole ingestion after the data has already landed.
        logger.error("batch %s ingested but publishing failed: %s", batch_id, exc)
        return False

    repository.mark_events_published(conn, batch_id)
    logger.info("batch %s published %d event(s)", batch_id, rows_ingested + 1)
    return True
