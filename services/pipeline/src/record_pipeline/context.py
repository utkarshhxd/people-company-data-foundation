"""Everything that is the same for every record, worked out once.

This is the first rule of making record-at-a-time affordable: per-record work
must only touch what actually varies per record. The column mapping, the
canonical field specs, the source and batch identity, the ruleset — all of it is
a property of the *layout*, not of the row. Loading any of it inside the loop
would multiply it by the record count for no gain.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from common.canonical import CANONICAL_SCHEMA_VERSION, column_fingerprint
from mapping import repository as mapping_repo
from mapping.engine import map_columns
from normalization import repository as norm_repo

logger = logging.getLogger(__name__)

# How many rows are read ahead of processing so value analysis has something to
# look at. The batch path samples this many rows back out of raw_record; here
# they are still in hand, so the mapping is derived from exactly the same
# evidence without a round-trip.
SAMPLE_SIZE = mapping_repo.SAMPLE_SIZE


@dataclass
class RunContext:
    source_id: str
    batch_id: str
    entity_type: str
    columns: list[str]
    source_schema_id: str
    # source_column -> {canonical_field, mapping_status}
    mappings: dict[str, dict[str, Any]] = field(default_factory=dict)
    mapping_reused: bool = False
    mapping_counts: dict[str, int] = field(default_factory=dict)

    @property
    def unmapped_columns(self) -> list[str]:
        """Columns carried through as observations but not presented as canonical."""
        return sorted(
            column
            for column, mapping in self.mappings.items()
            if mapping["canonical_field"] is None
            or mapping["mapping_status"] not in norm_repo.ACCEPTED_MAPPING_STATUSES
        )


def samples_from_rows(rows: list[dict[str, str | None]], columns: list[str]) -> dict[str, list[str]]:
    """Value-analysis samples taken from rows already in memory.

    Deliberately mirrors mapping.repository.sample_values: same rows (the first
    SAMPLE_SIZE of the file, in order), same skip of absent cells. If these two
    diverged, the same file would map differently depending on which path read
    it, which is exactly the reproducibility the mapping stage is built on.
    """
    samples: dict[str, list[str]] = {column: [] for column in columns}
    for row in rows[:SAMPLE_SIZE]:
        for column in columns:
            value = row.get(column)
            if value is not None:
                samples[column].append(str(value))
    return samples


def prepare(
    conn,
    source_id: str,
    batch_id: str,
    entity_type: str,
    columns: list[str],
    head_rows: list[dict[str, str | None]],
) -> RunContext:
    """Resolve this layout's mapping, reusing it if the layout has been seen.

    Reuse is what makes human review worth doing: a corrected mapping applies to
    every future file with these columns, and re-deriving it here would throw
    that work away.
    """
    fingerprint = column_fingerprint(columns)
    existing = mapping_repo.find_source_schema(
        conn, source_id, fingerprint, CANONICAL_SCHEMA_VERSION
    )
    reused = existing is not None

    if existing is None:
        mappings = map_columns(columns, entity_type, samples_from_rows(head_rows, columns))
        existing = mapping_repo.create_source_schema(
            conn, source_id, batch_id, entity_type, fingerprint, columns,
            CANONICAL_SCHEMA_VERSION,
        )
        mapping_repo.insert_mappings(conn, existing, mappings)
        conn.commit()
        logger.info("derived a new mapping for %d column(s)", len(mappings))
    else:
        logger.info("reusing source_schema %s for this layout", existing)

    return RunContext(
        source_id=source_id,
        batch_id=batch_id,
        entity_type=entity_type,
        columns=columns,
        source_schema_id=existing,
        mappings=norm_repo.get_column_mappings(conn, existing),
        mapping_reused=reused,
        mapping_counts=mapping_repo.mapping_counts(conn, existing),
    )
