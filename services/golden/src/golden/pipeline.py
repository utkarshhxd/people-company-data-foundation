"""Build the golden record for entities.

Rebuilding is idempotent by design: a value that has not changed keeps its
existing `valid_from`, so re-running does not churn history or make it look as
though something changed when nothing did. Only a genuinely different value
closes the old row and opens a new one.
"""

import logging
from dataclasses import dataclass

from common.db import connect

from golden import repository
from golden.strategies import choose

logger = logging.getLogger(__name__)


class NothingToBuild(Exception):
    pass


@dataclass
class BuildResult:
    entities: int
    written: int
    unchanged: int
    retired: int
    counts: dict[str, int]


def build_entity(conn, entity_id: str, entity_type: str) -> tuple[int, int, int]:
    """Recompute one entity's golden record. Returns (written, unchanged, retired)."""
    observations = repository.observations_for_entity(conn, entity_id)
    current = repository.current_values(conn, entity_id)
    written = unchanged = retired = 0

    for canonical_field, field_observations in observations.items():
        choice = choose(entity_type, canonical_field, field_observations)
        if choice is None:
            continue

        existing = current.get(canonical_field)
        if existing is not None and existing["value"] == choice.value:
            # Same answer as before. Leaving the row untouched keeps valid_from
            # meaning "since when has this been true", which is the only reading
            # that makes the history useful.
            unchanged += 1
            continue

        # Close the outgoing value first: only one row per (entity, field) may be
        # current, and the partial unique index enforces it.
        if existing is not None:
            repository.close_value(conn, str(existing["golden_id"]))
        golden_id = repository.insert_value(
            conn, entity_id, entity_type, canonical_field, choice
        )
        if existing is not None:
            repository.link_supersession(conn, str(existing["golden_id"]), golden_id)
        written += 1

    # A field with no supporting observation left stops being current. This
    # happens after a merge changes which records back an entity.
    for canonical_field, existing in current.items():
        if canonical_field not in observations:
            repository.retire_field(conn, str(existing["golden_id"]))
            retired += 1

    return written, unchanged, retired


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
    written = unchanged = retired = 0
    for entity in entities:
        w, u, r = build_entity(
            conn, str(entity["entity_id"]), entity["entity_type"]
        )
        written += w
        unchanged += u
        retired += r
        # Per entity: a failure part-way leaves earlier entities correctly built
        # rather than rolling back work that was already right.
        conn.commit()

    counts = repository.golden_counts(conn)
    logger.info(
        "golden record built for %d entity(ies): %d written, %d unchanged, "
        "%d retired %s",
        len(entities), written, unchanged, retired, counts,
    )
    return BuildResult(len(entities), written, unchanged, retired, counts)
