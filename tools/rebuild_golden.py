"""Rebuild golden values for entities whose confidence could have changed.

Golden records are derived, never authored, so they can always be recomputed
from the observations underneath them. That is what makes a change to the
survivorship or confidence rules safe to make at all: nothing has to be
migrated, only recalculated.

Only entities with more than one competing value are touched. An uncontested
field's confidence is its source's reliability plus an agreement bonus, and
neither of those changed -- so rebuilding the other 295,000 entities would cost
an hour to write back exactly what is already there.

Rebuilding is idempotent by construction: an unchanged value keeps its
valid_from, so a value nobody disagreed about does not acquire a new history
row just because the builder ran. History records when something became true,
not when it was last recomputed.

    docker compose run --rm pipeline python /tools/rebuild_golden.py
    docker compose run --rm pipeline python /tools/rebuild_golden.py --dry-run
"""

import argparse
import logging
import time

from common.db import connect
from golden.pipeline import build_entity

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

PROGRESS_EVERY = 1000


def affected_entities(conn) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT g.entity_id, e.entity_type
            FROM golden_attribute g
            JOIN entity e ON e.entity_id = g.entity_id
            WHERE g.valid_to IS NULL
              AND g.competing_values > 1
              AND e.status = 'active'
            ORDER BY g.entity_id
            """
        )
        return [(str(row[0]), row[1]) for row in cur]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be rebuilt and stop")
    parser.add_argument("--limit", type=int, help="stop after this many entities")
    args = parser.parse_args()

    with connect() as conn:
        entities = affected_entities(conn)
        if args.limit:
            entities = entities[: args.limit]
        print(f"{len(entities)} entit(ies) with contested values")
        if args.dry_run:
            return

        start = time.time()
        written = refreshed = unchanged = 0
        for index, (entity_id, entity_type) in enumerate(entities, start=1):
            try:
                w, r, u, _retired = build_entity(conn, entity_id, entity_type)
            except Exception as exc:
                # One entity failing must not cost the rebuild. Its old values
                # stay exactly as they were, which is the safe direction.
                conn.rollback()
                print(f"  {entity_id}: {type(exc).__name__}: {exc}")
                continue
            written += w
            refreshed += r
            unchanged += u
            # Per entity, so an interrupted rebuild leaves finished entities
            # finished rather than rolling all of them back.
            conn.commit()

            if index % PROGRESS_EVERY == 0:
                rate = index / (time.time() - start)
                print(f"  {index}/{len(entities)}  {rate:.0f} entities/s")

        elapsed = time.time() - start
        print(f"\nrebuilt {len(entities)} entit(ies) in {elapsed:.0f}s")
        print(f"  values written   {written}")
        print(f"  values refreshed {refreshed}")
        print(f"  values unchanged {unchanged}")


if __name__ == "__main__":
    main()
