"""Prove two processes can build the same entity's golden record at once.

This is a real race, not a hypothetical one: it was hit by running a batch CLI
while the Kafka consumer chain was processing the same batch. Both builders read
the current values, both decide the same row must be closed, both insert the
replacement, and the partial unique index permitting one current value per field
correctly refuses the second -- so one of them dies.

Reproducing it needs genuine concurrency against a real database, which is why
this is a script rather than a unit test. The unit suites run without Postgres
on purpose, and a mocked race proves nothing about whether Postgres serializes.

    docker compose run --rm pipeline python /tools/concurrency_check.py
    docker compose run --rm pipeline python /tools/concurrency_check.py --no-lock

--no-lock skips the lock so the original failure can be observed. Expect it to
fail; that is the point of having it.

Getting the race to happen at all needs care. Builders given identical inputs
compute identical values, take the idempotent refresh path, and never reach the
close-then-insert that the race lives in -- so simply running four of them
concurrently proves nothing, however many rounds it does. Each round therefore
drifts one stored value first, which makes every builder want to replace it, and
that is the contended path. The builders restore the correct value as they go.
"""

import argparse
import sys
import threading

from common.db import connect
from golden import repository
from golden.pipeline import build_entity

WORKERS = 4
ROUNDS = 5


def busiest_entity(conn) -> tuple[str, str]:
    """An entity with several fields and several records behind it.

    A thin entity would serialize trivially and prove nothing -- the race needs
    enough work between the read and the write for the two builders to overlap.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT g.entity_id, e.entity_type, count(*) AS fields
            FROM golden_attribute g
            JOIN entity e ON e.entity_id = g.entity_id
            WHERE g.valid_to IS NULL AND e.status = 'active'
            GROUP BY 1, 2
            ORDER BY fields DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row is None:
            sys.exit("no golden records to contend over; load a file first")
        return str(row[0]), row[1]


def drift_one_value(conn, entity_id: str) -> None:
    """Make one current value wrong, so every builder wants to replace it.

    Without this the builders agree with what is stored and take the refresh
    path, which is idempotent and uncontended. The value is corrected by the
    first builder to run; the drift only exists to open the window.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE golden_attribute SET value = value || '~drift'
            WHERE golden_id = (
                SELECT golden_id FROM golden_attribute
                WHERE entity_id = %s AND valid_to IS NULL
                ORDER BY canonical_field LIMIT 1
            )
            """,
            (entity_id,),
        )


def build_repeatedly(entity_id: str, entity_type: str, use_lock: bool, errors: list):
    try:
        with connect() as conn:
            for _ in range(ROUNDS):
                drift_one_value(conn, entity_id)
                conn.commit()
                if use_lock:
                    build_entity(conn, entity_id, entity_type)
                else:
                    # The pre-lock code path, reconstructed: read what is
                    # current, then write, with nothing holding the gap.
                    original = repository.lock_entity
                    repository.lock_entity = lambda *a, **k: None
                    try:
                        build_entity(conn, entity_id, entity_type)
                    finally:
                        repository.lock_entity = original
                conn.commit()
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")


def current_value_counts(conn, entity_id: str) -> dict[str, int]:
    """Fields with more than one current value. Should always be empty."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT canonical_field, count(*)
            FROM golden_attribute
            WHERE entity_id = %s AND valid_to IS NULL
            GROUP BY 1 HAVING count(*) > 1
            """,
            (entity_id,),
        )
        return {field: count for field, count in cur}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-lock", action="store_true",
                        help="reproduce the original failure")
    args = parser.parse_args()
    use_lock = not args.no_lock

    with connect() as conn:
        entity_id, entity_type = busiest_entity(conn)

    print(f"entity      {entity_id} ({entity_type})")
    print(f"builders    {WORKERS} concurrent, {ROUNDS} rounds each")
    print(f"lock        {'held per entity' if use_lock else 'DISABLED'}")
    print()

    errors: list[str] = []
    threads = [
        threading.Thread(target=build_repeatedly,
                         args=(entity_id, entity_type, use_lock, errors))
        for _ in range(WORKERS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with connect() as conn:
        duplicates = current_value_counts(conn, entity_id)

    if errors:
        print(f"{len(errors)} builder(s) failed:")
        for error in errors[:5]:
            print(f"  {error}")
    if duplicates:
        print(f"fields left with more than one current value: {duplicates}")

    if not errors and not duplicates:
        print("OK: every builder completed and one current value per field remains")
        return 0
    print("\nFAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
