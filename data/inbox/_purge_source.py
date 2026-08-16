"""Remove everything belonging to named sources. For test/benchmark data only.

Nothing in the system deletes source data by design — invalid records must not
disappear. This exists so that data created purely to measure or demonstrate the
pipeline can be taken back out again, and it is scoped strictly to the source
names passed on the command line.
"""

import sys

from common.db import connect

_SCOPED_ENTITIES = """
SELECT e.entity_id FROM entity e
JOIN raw_record r ON r.record_id = e.first_seen_record_id
JOIN batch b ON b.batch_id = r.batch_id
JOIN source s ON s.source_id = b.source_id
WHERE s.source_name = %s
"""

RECORD_CHILDREN = ("validation_result", "record_validation", "quarantine_event",
                   "quarantine_item", "match_candidate", "record_entity_link",
                   "attribute_observation")


def purge(conn, names: list[str]) -> None:
    """Phased across all the names, not one source at a time: one source's
    record can be linked to another's entity, so every link must go before any
    entity does."""
    with conn.cursor() as cur:
        for name in names:
            for table in RECORD_CHILDREN:
                cur.execute(
                    f"""
                    DELETE FROM {table} t USING raw_record r, batch b, source s
                    WHERE t.record_id = r.record_id AND r.batch_id = b.batch_id
                      AND b.source_id = s.source_id AND s.source_name = %s
                    """,
                    (name,),
                )
            cur.execute(
                """
                DELETE FROM record_error e USING batch b, source s
                WHERE e.batch_id = b.batch_id AND b.source_id = s.source_id
                  AND s.source_name = %s
                """,
                (name,),
            )
        for name in names:
            for table, column in (("golden_attribute", "entity_id"),
                                  ("entity_merge", "merged_entity_id"),
                                  ("entity_merge", "surviving_entity_id"),
                                  ("entity_identity_key", "entity_id")):
                cur.execute(
                    f"DELETE FROM {table} WHERE {column} IN ({_SCOPED_ENTITIES})", (name,)
                )
            cur.execute(
                f"DELETE FROM entity WHERE entity_id IN ({_SCOPED_ENTITIES})", (name,)
            )
        for name in names:
            cur.execute(
                """
                DELETE FROM raw_record r USING batch b, source s
                WHERE r.batch_id = b.batch_id AND b.source_id = s.source_id
                  AND s.source_name = %s
                """,
                (name,),
            )
            cur.execute(
                """
                DELETE FROM column_mapping m USING source_schema ss, source s
                WHERE m.source_schema_id = ss.source_schema_id
                  AND ss.source_id = s.source_id AND s.source_name = %s
                """,
                (name,),
            )
            cur.execute(
                "DELETE FROM source_schema ss USING source s "
                "WHERE ss.source_id = s.source_id AND s.source_name = %s", (name,)
            )
            cur.execute(
                "DELETE FROM batch b USING source s "
                "WHERE b.source_id = s.source_id AND s.source_name = %s", (name,)
            )
            cur.execute("DELETE FROM source WHERE source_name = %s", (name,))
    conn.commit()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: _purge_source.py <source_name> [...]", file=sys.stderr)
        raise SystemExit(2)
    with connect() as connection:
        purge(connection, sys.argv[1:])
    print(f"purged: {', '.join(sys.argv[1:])}")
