"""Turn raw records into normalized attribute observations.

The guarantee this module exists to provide: every column of every row produces
at least one observation. A column that mapped to nothing, a cell holding a
'NULL' placeholder, a cell holding three values — all of them are recorded. The
only thing normalization decides is what a value looks like once made
comparable; what it *means* is decided later, with the raw value still on hand.
"""

import logging
from dataclasses import dataclass

from common.canonical import (
    CANONICAL_SCHEMA_VERSION,
    SELF,
    column_fingerprint,
    field_by_name,
)
from common.db import connect

from normalization import repository
from normalization.normalizers import normalize, split_values

logger = logging.getLogger(__name__)

BATCH_INSERT_SIZE = 2000


class BatchNotNormalizable(Exception):
    pass


@dataclass
class NormalizeResult:
    batch_id: str
    source_schema_id: str
    records: int
    observations: int
    counts: dict[str, int]


def _value_type_for(
    entity_type: str, canonical_field: str | None, subject: str = SELF
) -> str:
    if canonical_field is None:
        return "text"
    spec = field_by_name(entity_type, canonical_field, subject)
    return spec.value_type if spec else "text"


def observations_for_record(
    record_id, payload: dict, mappings: dict, entity_type: str, observed_at,
    batch_id: str, source_id: str,
) -> list[tuple]:
    """Every column of one row -> one or more observation rows."""
    rows: list[tuple] = []
    for source_column, raw in payload.items():
        mapping = mappings.get(source_column)
        status = mapping["mapping_status"] if mapping else "unmapped"
        # Only a confirmed mapping labels the value as canonical. An unreviewed
        # guess still gets stored — it just isn't presented as truth.
        confirmed = (
            mapping is not None
            and mapping["mapping_status"] in repository.ACCEPTED_MAPPING_STATUSES
        )
        canonical_field = mapping["canonical_field"] if confirmed else None
        # Whose attribute this is. An unconfirmed mapping has no canonical field
        # and therefore no subject to speak of, so it records the record's own —
        # nothing downstream reads a subject without a field beside it.
        subject = mapping.get("subject", SELF) if confirmed else SELF
        value_type = _value_type_for(entity_type, canonical_field, subject)

        # A missing cell is still an observation: the source said nothing here.
        if raw is None:
            rows.append((
                record_id, batch_id, source_id, entity_type, source_column,
                canonical_field, status, 0, "", None, value_type,
                f"{value_type}:absent", True, observed_at, subject,
            ))
            continue

        raw_text = str(raw)
        for index, part in enumerate(split_values(raw_text, value_type)):
            result = normalize(part, value_type)
            rows.append((
                record_id, batch_id, source_id, entity_type, source_column,
                canonical_field, status, index, part, result.normalized_value,
                value_type, result.method, result.is_null_token, observed_at, subject,
            ))
    return rows


def normalize_batch(batch_id: str) -> NormalizeResult:
    with connect() as conn:
        batch = repository.get_batch(conn, batch_id)
        if batch is None:
            raise BatchNotNormalizable(f"batch {batch_id} not found")
        if batch["status"] != "completed":
            raise BatchNotNormalizable(
                f"batch {batch_id} has status {batch['status']!r}, expected 'completed'"
            )

        columns = repository.batch_columns(conn, batch_id)
        if not columns:
            raise BatchNotNormalizable(f"batch {batch_id} has no records")

        source_id = str(batch["source_id"])
        entity_type = batch["entity_type"]
        source_schema_id = repository.find_source_schema(
            conn, source_id, column_fingerprint(columns), CANONICAL_SCHEMA_VERSION
        )
        if source_schema_id is None:
            raise BatchNotNormalizable(
                f"batch {batch_id} has no mapped schema for canonical version "
                f"{CANONICAL_SCHEMA_VERSION}; run schema mapping first"
            )

        mappings = repository.get_column_mappings(conn, source_schema_id)

        pending: list[tuple] = []
        records = observations = 0
        # A separate connection reads the cursor so the writes below don't
        # invalidate the stream mid-iteration.
        with connect() as read_conn:
            for record_id, payload, ingested_at in repository.iter_records(
                read_conn, batch_id
            ):
                records += 1
                pending.extend(observations_for_record(
                    record_id, payload, mappings, entity_type, ingested_at,
                    batch_id, source_id,
                ))
                if len(pending) >= BATCH_INSERT_SIZE:
                    observations += repository.insert_observations(conn, pending)
                    conn.commit()
                    pending = []

        observations += repository.insert_observations(conn, pending)
        conn.commit()

        counts = repository.observation_counts(conn, batch_id)
        logger.info(
            "batch %s normalized: %d record(s) -> %d observation(s) %s",
            batch_id, records, observations, counts,
        )
        return NormalizeResult(
            batch_id, source_schema_id, records, observations, counts
        )
