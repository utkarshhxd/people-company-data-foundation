import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from common.db import connect

from ingestion import repository
from ingestion.readers import DEFAULT_BATCH_SIZE, iter_file

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


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_record_ids(
    rows: list[dict[str, str | None]], record_id_column: str | None, offset: int = 0
) -> list[str]:
    """Fallback ids are row numbers, so `offset` keeps them continuous across batches."""
    if record_id_column is None:
        return [str(offset + index + 1) for index in range(len(rows))]
    if rows and record_id_column not in rows[0]:
        raise KeyError(f"--record-id-column {record_id_column!r} is not a column in this file")
    return [
        (row.get(record_id_column) or str(offset + index + 1))
        for index, row in enumerate(rows)
    ]


def ingest(
    path: Path,
    entity_type: str,
    source_name: str,
    source_type: str,
    reliability: float,
    record_id_column: str | None = None,
    allow_reingest: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    describes: str | None = None,
    sheet: str | None = None,
) -> IngestResult:
    digest = file_hash(path)
    size = path.stat().st_size

    with connect() as conn:
        source_id = repository.get_or_create_source(
            conn, source_name, source_type, reliability, describes
        )
        conn.commit()

        # Identity is the file's CONTENT, not its name: a renamed copy is still
        # the same file, and a changed file with the same name is not.
        previous = repository.find_completed_batch(conn, source_id, digest)
        if previous and not allow_reingest:
            raise ReingestBlocked(
                f"{path.name} was already ingested for source {source_name!r} "
                f"as batch {previous}. Re-run with --allow-reingest to ingest it again."
            )

        # An in-flight batch of the same file is never intentional, so
        # --allow-reingest does not override it: waiting is always correct.
        in_flight = repository.find_in_flight_batch(conn, source_id, digest)
        if in_flight:
            raise ReingestBlocked(
                f"{path.name} is currently being ingested for source {source_name!r} "
                f"as batch {in_flight}. Wait for it to finish, or mark it failed "
                f"if it is stuck."
            )

        # Same content under a different vendor name is legitimate but usually a
        # typo, so it warns rather than blocks.
        elsewhere = repository.find_batches_from_other_sources(conn, source_id, digest)
        if elsewhere:
            logger.warning(
                "%s has identical content to %d batch(es) already ingested under "
                "other source(s): %s. Continuing — but check --source-name is right.",
                path.name, len(elsewhere),
                ", ".join(f"{name} ({batch})" for name, batch in elsewhere),
            )

        batch_id = repository.create_batch(
            conn, source_id, entity_type, str(path), path.name, digest, size
        )
        conn.commit()
        logger.info("batch %s started for %s", batch_id, path.name)

        rows_read = rows_ingested = 0
        try:
            # Streamed and committed a batch at a time. Loading the whole file
            # first cost memory proportional to the file and put a 20,000-row
            # ingest in a single transaction; neither is necessary, because a
            # batch only becomes visible downstream once its status is
            # 'completed', which happens after the last batch lands.
            for columns, rows in iter_file(path, batch_size, sheet):
                # Recorded from the first batch. The reader's order is the file's
                # order, and it is what the layout's fingerprint is taken over --
                # raw_payload is jsonb and will not give it back.
                if rows_read == 0:
                    repository.set_batch_columns(conn, batch_id, columns)

                source_record_ids = _source_record_ids(
                    rows, record_id_column, offset=rows_read
                )
                records = [
                    (source_record_ids[index], rows_read + index + 1, row)
                    for index, row in enumerate(rows)
                ]
                rows_ingested += repository.insert_raw_records(
                    conn, batch_id, source_id, entity_type, records
                )
                rows_read += len(rows)
                conn.commit()
                logger.debug("batch %s: %d row(s) so far", batch_id, rows_read)
        except Exception as exc:
            # Rows from completed batches stay: they are what the source said,
            # and deleting them would lose the only evidence of a partial load.
            # The batch is marked 'failed', and every downstream stage requires
            # 'completed', so partial rows are inert rather than dangerous.
            conn.rollback()
            repository.fail_batch(conn, batch_id, f"{type(exc).__name__}: {exc}")
            conn.commit()
            logger.error(
                "batch %s failed after %d row(s): %s", batch_id, rows_ingested, exc
            )
            raise

        repository.finish_batch(conn, batch_id, rows_read, rows_ingested, 0)
        conn.commit()
        logger.info("batch %s committed %d record(s)", batch_id, rows_ingested)

    return IngestResult(
        batch_id=batch_id,
        source_id=source_id,
        rows_read=rows_read,
        rows_ingested=rows_ingested,
        rows_skipped=0,
    )
    return True
