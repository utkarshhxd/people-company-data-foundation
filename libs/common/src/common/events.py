"""Kafka topic names and event builders.

Events carry references only — never source payloads. Postgres is the source of
truth; these events tell downstream stages which committed rows to go read.
"""

from datetime import datetime
from typing import Any

TOPIC_RAW_RECORD_INGESTED = "pcdf.raw_record.ingested"
TOPIC_BATCH_INGESTED = "pcdf.batch.ingested"
TOPIC_SCHEMA_MAPPED = "pcdf.schema.mapped"
TOPIC_RECORDS_NORMALIZED = "pcdf.records.normalized"
TOPIC_RECORDS_VALIDATED = "pcdf.records.validated"
TOPIC_ENTITIES_RESOLVED = "pcdf.entities.resolved"
TOPIC_GOLDEN_UPDATED = "pcdf.golden.updated"

ALL_TOPICS = (
    TOPIC_RAW_RECORD_INGESTED,
    TOPIC_BATCH_INGESTED,
    TOPIC_SCHEMA_MAPPED,
    TOPIC_RECORDS_NORMALIZED,
    TOPIC_RECORDS_VALIDATED,
    TOPIC_ENTITIES_RESOLVED,
    TOPIC_GOLDEN_UPDATED,
)

EVENT_VERSION = 1


def _isoformat(value: datetime) -> str:
    return value.isoformat()


def raw_record_ingested(
    record_id: str,
    batch_id: str,
    source_id: str,
    entity_type: str,
    ingested_at: datetime,
) -> dict[str, Any]:
    return {
        "event_type": "raw_record.ingested",
        "event_version": EVENT_VERSION,
        "record_id": record_id,
        "batch_id": batch_id,
        "source_id": source_id,
        "entity_type": entity_type,
        "ingested_at": _isoformat(ingested_at),
    }


def records_normalized(
    batch_id: str,
    source_schema_id: str,
    source_id: str,
    entity_type: str,
    records: int,
    observations: int,
    counts: dict[str, int],
    normalized_at: datetime,
) -> dict[str, Any]:
    return {
        "event_type": "records.normalized",
        "event_version": EVENT_VERSION,
        "batch_id": batch_id,
        "source_schema_id": source_schema_id,
        "source_id": source_id,
        "entity_type": entity_type,
        "records": records,
        "observations": observations,
        "counts": counts,
        "normalized_at": _isoformat(normalized_at),
    }


def records_validated(
    batch_id: str,
    source_id: str,
    entity_type: str,
    records: int,
    judgements: int,
    counts: dict[str, int],
    ruleset_version: str,
    validated_at: datetime,
) -> dict[str, Any]:
    return {
        "event_type": "records.validated",
        "event_version": EVENT_VERSION,
        "batch_id": batch_id,
        "source_id": source_id,
        "entity_type": entity_type,
        "records": records,
        "judgements": judgements,
        # Counts, not record ids: an invalid record is read from Postgres by the
        # quarantine stage, never carried in the event.
        "counts": counts,
        "ruleset_version": ruleset_version,
        "validated_at": _isoformat(validated_at),
    }


def entities_resolved(
    batch_id: str,
    source_id: str,
    entity_type: str,
    records: int,
    linked: int,
    created: int,
    candidates: int,
    counts: dict[str, int],
    resolved_at: datetime,
) -> dict[str, Any]:
    return {
        "event_type": "entities.resolved",
        "event_version": EVENT_VERSION,
        "batch_id": batch_id,
        "source_id": source_id,
        "entity_type": entity_type,
        "records": records,
        "linked": linked,
        "created": created,
        "candidates": candidates,
        "counts": counts,
        "resolved_at": _isoformat(resolved_at),
    }


def golden_updated(
    batch_id: str,
    entity_type: str,
    entities: int,
    values_written: int,
    values_refreshed: int,
    values_unchanged: int,
    values_retired: int,
    counts: dict[str, int],
    updated_at: datetime,
) -> dict[str, Any]:
    return {
        "event_type": "golden.updated",
        "event_version": EVENT_VERSION,
        "batch_id": batch_id,
        "entity_type": entity_type,
        "entities": entities,
        "values_written": values_written,
        # Same value, stronger support: not a change, but not a no-op either.
        "values_refreshed": values_refreshed,
        "values_unchanged": values_unchanged,
        "values_retired": values_retired,
        "counts": counts,
        "updated_at": _isoformat(updated_at),
    }


def schema_mapped(
    source_schema_id: str,
    batch_id: str,
    source_id: str,
    entity_type: str,
    counts: dict[str, int],
    schema_version: str,
    mapped_at: datetime,
) -> dict[str, Any]:
    return {
        "event_type": "schema.mapped",
        "event_version": EVENT_VERSION,
        "source_schema_id": source_schema_id,
        "batch_id": batch_id,
        "source_id": source_id,
        "entity_type": entity_type,
        "counts": counts,
        "schema_version": schema_version,
        "mapped_at": _isoformat(mapped_at),
    }


def batch_ingested(
    batch_id: str,
    source_id: str,
    entity_type: str,
    file_name: str,
    rows_ingested: int,
    completed_at: datetime,
) -> dict[str, Any]:
    return {
        "event_type": "batch.ingested",
        "event_version": EVENT_VERSION,
        "batch_id": batch_id,
        "source_id": source_id,
        "entity_type": entity_type,
        "file_name": file_name,
        "rows_ingested": rows_ingested,
        "completed_at": _isoformat(completed_at),
    }
