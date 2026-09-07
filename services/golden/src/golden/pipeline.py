"""Build the golden record for entities.

Rebuilding is idempotent by design: a value that has not changed keeps its
existing `valid_from`, so re-running does not churn history or make it look as
though something changed when nothing did. Only a genuinely different value
closes the old row and opens a new one.
"""

import logging
from dataclasses import dataclass

from common.db import connect
from psycopg.types.json import Json

from golden import repository
from golden.strategies import choose

logger = logging.getLogger(__name__)

# Entities per commit. build_entity is idempotent (an unchanged value keeps its
# valid_from and writes nothing new), and build_batch always reprocesses every
# entity entities_for_batch returns regardless of what a previous attempt
# already committed -- so a crash mid-batch already means "redo the batch"
# either way, at any commit granularity. Batching just cuts the fsync count.
COMMIT_BATCH_SIZE = 500


class NothingToBuild(Exception):
    pass


@dataclass
class BuildResult:
    entities: int
    written: int
    refreshed: int
    unchanged: int
    retired: int
    counts: dict[str, int]


def build_entity(conn, entity_id: str, entity_type: str) -> tuple[int, int, int, int]:
    """Recompute one entity's golden record.

    Returns (written, refreshed, unchanged, retired).
    """
    # Before the read, not after: everything below decides what to write by
    # comparing against what is currently there, so the lock has to cover the
    # read as well as the write or it protects nothing.
    repository.lock_entity(conn, entity_id)

    observations = repository.observations_for_entity(conn, entity_id)
    current = repository.current_values(conn, entity_id)
    written = refreshed = unchanged = retired = 0

    # Brand-new fields (no current row to supersede) and refreshed-evidence
    # fields (same value, new support) are batched: neither needs a result
    # back before the next field can be decided, so nothing about deciding
    # field N+1 depends on field N's write having happened yet. A changed
    # value stays row-at-a-time below, because link_supersession needs the
    # new row's golden_id back from insert_value.
    new_value_rows: list[tuple] = []
    refresh_rows: list[tuple] = []

    for canonical_field, field_observations in observations.items():
        choice = choose(entity_type, canonical_field, field_observations)
        if choice is None:
            continue

        existing = current.get(canonical_field)
        if existing is not None and existing["value"] == choice.value:
            # Same answer as before, so valid_from must not move: it means
            # "since when has this been true", which is the only reading that
            # makes the history useful. But the support behind the value can
            # still have changed — a new vendor agreeing raises confidence
            # without making the value newly true — so the evidence is
            # refreshed in place rather than left stale.
            if (existing["supporting_sources"] != choice.supporting_sources
                    or existing["competing_values"] != choice.competing_values
                    or float(existing["confidence"]) != choice.confidence):
                refresh_rows.append((
                    choice.confidence, choice.supporting_sources,
                    choice.competing_values, Json(choice.evidence),
                    choice.winning_record_id, choice.winning_source_id,
                    choice.raw_value, str(existing["golden_id"]),
                ))
                refreshed += 1
            else:
                unchanged += 1
            continue

        if existing is None:
            new_value_rows.append((
                entity_id, entity_type, canonical_field, choice.value,
                choice.raw_value, choice.strategy, choice.confidence,
                choice.winning_record_id, choice.winning_source_id,
                choice.supporting_sources, choice.competing_values,
                Json(choice.evidence),
            ))
            written += 1
            continue

        # A genuinely changed value: close the outgoing row first (only one row
        # per (entity, field) may be current, and the partial unique index
        # enforces it), then link the closed row to what replaced it. Kept
        # row-at-a-time because link_supersession needs insert_value's
        # RETURNING golden_id.
        repository.close_value(conn, str(existing["golden_id"]))
        golden_id = repository.insert_value(
            conn, entity_id, entity_type, canonical_field, choice
        )
        repository.link_supersession(conn, str(existing["golden_id"]), golden_id)
        written += 1

    repository.insert_values(conn, new_value_rows)
    repository.refresh_evidence_many(conn, refresh_rows)

    # A field with no supporting observation left stops being current. This
    # happens after a merge changes which records back an entity.
    retire_ids = [
        str(existing["golden_id"])
        for canonical_field, existing in current.items()
        if canonical_field not in observations
    ]
    repository.retire_fields(conn, retire_ids)
    retired = len(retire_ids)

    return written, refreshed, unchanged, retired


def build_batch(batch_id: str) -> BuildResult:
    with connect() as conn:
        entities = repository.entities_for_batch(conn, batch_id)
        if not entities:
            raise NothingToBuild(
                f"batch {batch_id} has no resolved entities; resolve it first"
            )
        return _build_all(conn, entities)


def build_one(entity_id: str) -> BuildResult:
    with connect() as conn:
        entity = repository.get_entity(conn, entity_id)
        if entity is None:
            raise NothingToBuild(f"no entity {entity_id}")
        if entity["status"] != "active":
            raise NothingToBuild(
                f"entity {entity_id} was merged into {entity['merged_into_entity_id']}; "
                "build that one instead"
            )
        return _build_all(conn, [entity])


def _build_all(conn, entities: list[dict]) -> BuildResult:
    written = refreshed = unchanged = retired = 0
    for i, entity in enumerate(entities, start=1):
        w, f, u, r = build_entity(
            conn, str(entity["entity_id"]), entity["entity_type"]
        )
        written += w
        refreshed += f
        unchanged += u
        retired += r
        # Batched, not per entity -- see COMMIT_BATCH_SIZE above.
        if i % COMMIT_BATCH_SIZE == 0:
            conn.commit()
    conn.commit()

    counts = repository.golden_counts(conn)
    logger.info(
        "golden record built for %d entity(ies): %d written, %d refreshed, "
        "%d unchanged, %d retired %s",
        len(entities), written, refreshed, unchanged, retired, counts,
    )
    return BuildResult(len(entities), written, refreshed, unchanged, retired, counts)
