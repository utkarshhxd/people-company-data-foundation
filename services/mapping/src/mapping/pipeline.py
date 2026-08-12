import logging
from dataclasses import dataclass

from common.canonical import CANONICAL_SCHEMA_VERSION
from common.db import connect

from mapping import repository
from mapping.engine import map_columns

logger = logging.getLogger(__name__)


class BatchNotMappable(Exception):
    pass


@dataclass
class MapResult:
    source_schema_id: str
    batch_id: str
    reused: bool
    counts: dict[str, int]


def map_batch(batch_id: str) -> MapResult:
    with connect() as conn:
        batch = repository.get_batch(conn, batch_id)
        if batch is None:
            raise BatchNotMappable(f"batch {batch_id} not found")
        # Only completed batches are safe to read: ingestion marks a batch
        # completed only after its records are committed.
        if batch["status"] != "completed":
            raise BatchNotMappable(
                f"batch {batch_id} has status {batch['status']!r}, expected 'completed'"
            )

        columns = repository.batch_columns(conn, batch_id)
        if not columns:
            raise BatchNotMappable(f"batch {batch_id} has no records to inspect")

        fingerprint = repository.column_fingerprint(columns)
        source_id = str(batch["source_id"])
        entity_type = batch["entity_type"]

        existing = repository.find_source_schema(
            conn, source_id, fingerprint, CANONICAL_SCHEMA_VERSION
        )
        if existing is not None:
            # Same layout seen before: reuse it, preserving any human review.
            logger.info("batch %s reuses source_schema %s", batch_id, existing)
            return MapResult(existing, batch_id, True, repository.mapping_counts(conn, existing))

        samples = repository.sample_values(conn, batch_id, columns)
        mappings = map_columns(columns, entity_type, samples)

        source_schema_id = repository.create_source_schema(
            conn, source_id, batch_id, entity_type, fingerprint, columns,
            CANONICAL_SCHEMA_VERSION,
        )
        repository.insert_mappings(conn, source_schema_id, mappings)
        conn.commit()

        counts = repository.mapping_counts(conn, source_schema_id)
        logger.info("batch %s mapped %d column(s): %s", batch_id, len(mappings), counts)
        return MapResult(source_schema_id, batch_id, False, counts)
