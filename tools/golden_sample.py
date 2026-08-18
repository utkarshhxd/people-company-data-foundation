"""Build golden records for a sample of one batch's entities.

Rebuilding a large batch means rebuilding every entity its records touch --
240,131 for the Apollo file -- which is right for a load and wasteful when the
question is only "did this change work". This walks a deterministic slice
instead, so a schema or survivorship change can be seen at realistic scale
without paying for the whole set.

Deterministic on purpose: ordering by entity_id means the same --limit always
picks the same entities, so two runs are comparable and a surprising result can
be looked at again rather than having moved.

    docker compose run --rm pipeline python /tools/golden_sample.py \
        --batch-id <id> --limit 40000

A full load still uses `golden build --batch-id`. This is for testing.
"""

import argparse
import logging
import time

from common.db import connect
from golden.pipeline import build_entity

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")


def entities_for(conn, batch_id: str, limit: int, only_missing: bool,
                 entity_type: str | None = None) -> list[tuple]:
    # Entities reachable from this batch's records. DISTINCT because one entity
    # is routinely backed by many records -- that is the point of the system.
    #
    # The type filter is applied here rather than after the query so that
    # --limit counts the entities being built, not the ones read past.
    clause = ""
    if entity_type is not None:
        clause += "\n              AND e.entity_type = %(entity_type)s"
    if only_missing:
        # Skip entities a previous run already rebuilt. Rebuilding is idempotent,
        # so this is a speed choice rather than a correctness one.
        clause = """
              AND NOT EXISTS (
                  SELECT 1 FROM golden_attribute g
                  WHERE g.entity_id = l.entity_id
                    AND g.valid_to IS NULL
                    AND g.canonical_field IN ('annual_revenue', 'total_funding',
                        'technologies', 'keywords', 'seo_description')
              )"""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT l.entity_id, e.entity_type
            FROM record_entity_link l
            JOIN raw_record r ON r.record_id = l.record_id
            JOIN entity e ON e.entity_id = l.entity_id
            WHERE r.batch_id = %(batch_id)s
              AND e.status = 'active'
              {clause}
            ORDER BY l.entity_id
            LIMIT %(limit)s
            """,
            {"batch_id": batch_id, "limit": limit, "entity_type": entity_type},
        )
        return [(str(row[0]), row[1]) for row in cur]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--limit", type=int, default=40000)
    parser.add_argument(
        "--company-only", action="store_true",
        help="only company entities -- the commercial fields land on those, and "
             "the person entities in a contact file outnumber them ten to one",
    )
    parser.add_argument(
        "--only-missing", action="store_true",
        help="skip entities that already carry a commercial field",
    )
    parser.add_argument("--progress-every", type=int, default=2000)
    args = parser.parse_args()

    with connect() as conn:
        targets = entities_for(
            conn, args.batch_id, args.limit, args.only_missing,
            "company" if args.company_only else None,
        )

    print(f"{len(targets)} entity(ies) to build")
    started = time.monotonic()
    written = refreshed = unchanged = retired = 0

    with connect() as conn:
        for done, (entity_id, entity_type) in enumerate(targets, start=1):
            w, f, u, r = build_entity(conn, entity_id, entity_type)
            written += w
            refreshed += f
            unchanged += u
            retired += r
            # One entity per transaction: the same guarantee the batch builder
            # gives, so an interrupted run leaves finished entities finished.
            conn.commit()

            if done % args.progress_every == 0:
                rate = done / (time.monotonic() - started)
                remaining = (len(targets) - done) / rate if rate else 0
                print(f"  {done}/{len(targets)}  {rate:.0f}/s  "
                      f"~{remaining / 60:.0f}m left", flush=True)

    elapsed = time.monotonic() - started
    print(f"\n{len(targets)} entity(ies) in {elapsed / 60:.1f}m")
    print(f"  written    {written}")
    print(f"  refreshed  {refreshed}")
    print(f"  unchanged  {unchanged}")
    print(f"  retired    {retired}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
