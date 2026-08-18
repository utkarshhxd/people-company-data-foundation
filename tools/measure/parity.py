"""Run one file down both paths and compare what they produced.

The per-record pipeline is only worth having if it means the same thing as the
batch pipeline. It reuses the same stage functions, but reuse is an intention,
not a proof: the per-record path reconstructs in memory several things the batch
path reads back out of Postgres, and a reconstruction can be subtly wrong.

So: same file, two sources, every derived artifact compared row by row.

Run with the consumers stopped:

    docker compose stop mapping normalization validation resolution golden
    docker compose run --rm pipeline python /data/inbox/_parity.py
    docker compose start mapping normalization validation resolution golden
"""

import random
import sys
import time
from pathlib import Path

from common.db import connect
from golden.pipeline import build_batch
from ingestion.pipeline import ingest
from mapping.pipeline import map_batch
from normalization.pipeline import normalize_batch
from record_pipeline.runner import run_file
from resolution.pipeline import resolve_batch
from validation.pipeline import validate_batch

ROWS = 500
BATCH_SOURCE = "_parity_batch"
RECORD_SOURCE = "_parity_record"

COLUMNS = ["org_name", "e_mail", "mobile_no", "web", "street", "city",
           "postal_code", "employees", "founded", "notes"]


def generate(path: Path) -> None:
    """Deliberately messy: the defects are where the two paths could diverge."""
    random.seed(11)
    lines = [",".join(COLUMNS)]
    for i in range(1, ROWS + 1):
        name = f"Company {i}"
        email = f"info{i}@company{i}.com" if i % 7 else "N/A"
        # Filler phone every 13th, missing every 11th.
        phone = "0000000000" if i % 13 == 0 else (
            "" if i % 11 == 0 else f"+1 555 {i:04d}")
        web = f"https://company{i}.com" if i % 5 else ""
        # Lost whitespace every 17th — the address.spacing rule.
        street = f"{i}MainStreetNW" if i % 17 == 0 else f"{i} Main Street NW"
        postal = "00501" if i % 3 == 0 else f"{10000 + i}"
        employees = "not known" if i % 23 == 0 else str(i * 3)
        founded = "1899" if i % 29 == 0 else str(1950 + (i % 70))
        notes = "quoted, comma" if i % 19 == 0 else f"note {i}"
        lines.append(
            f'"{name}","{email}","{phone}","{web}","{street}","City {i % 40}",'
            f'"{postal}","{employees}","{founded}","{notes}"'
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_batch_path(path: Path) -> tuple[str, float]:
    """Returns (batch_id, seconds spent working).

    The wait for the mapping consumer is excluded from the timing: it measures
    how quickly a Kafka consumer woke up, not how quickly the batch path
    processes records.
    """
    started = time.perf_counter()
    result = ingest(
        path=path, entity_type="company", source_name=BATCH_SOURCE,
        source_type="csv", reliability=0.5, allow_reingest=True,
    )
    batch_id = result.batch_id
    seconds = time.perf_counter() - started

    # Every stage is driven explicitly here, which is only safe with the
    # consumers stopped. Left running, they react to the same events and
    # process this batch alongside us — two golden builders on one entity
    # violate the single-current-value index, and the comparison would be
    # measuring a race rather than the two paths.
    map_batch(batch_id)

    started = time.perf_counter()
    normalize_batch(batch_id)
    validate_batch(batch_id)
    resolve_batch(batch_id)
    build_batch(batch_id)
    return batch_id, seconds + (time.perf_counter() - started)


def snapshot(conn, batch_id: str) -> dict[int, dict]:
    """Everything derived, keyed by the row's position in the file."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.row_number,
                   count(o.observation_id)                                    AS observations,
                   count(o.canonical_field)                                   AS canonical,
                   count(*) FILTER (WHERE o.is_null_token)                    AS null_tokens
            FROM raw_record r
            LEFT JOIN attribute_observation o ON o.record_id = r.record_id
            WHERE r.batch_id = %s
            GROUP BY r.row_number
            """,
            (batch_id,),
        )
        rows = {
            n: {"observations": o, "canonical": c, "null_tokens": t}
            for n, o, c, t in cur
        }

        cur.execute(
            """
            SELECT r.row_number, v.status, v.error_count, v.warning_count,
                   v.validated_attributes, v.unvalidated_attributes, v.failed_rules
            FROM raw_record r JOIN record_validation v ON v.record_id = r.record_id
            WHERE r.batch_id = %s
            """,
            (batch_id,),
        )
        for n, status, errors, warnings, validated, unvalidated, failed in cur:
            rows[n].update({
                "status": status, "errors": errors, "warnings": warnings,
                "validated": validated, "unvalidated": unvalidated,
                "failed_rules": tuple(failed or ()),
            })

        cur.execute(
            """
            SELECT r.row_number, count(*)
            FROM raw_record r JOIN validation_result vr ON vr.record_id = r.record_id
            WHERE r.batch_id = %s GROUP BY r.row_number
            """,
            (batch_id,),
        )
        for n, count in cur:
            rows[n]["judgements"] = count

        # The identity keys a record contributed. Entity ids necessarily differ
        # between the two runs; the keys built from the record do not.
        cur.execute(
            """
            SELECT r.row_number, k.key_type, k.key_value
            FROM raw_record r JOIN entity_identity_key k ON k.source_record_id = r.record_id
            WHERE r.batch_id = %s
            """,
            (batch_id,),
        )
        for n, key_type, key_value in cur:
            rows[n].setdefault("keys", set()).add((key_type, key_value))

        cur.execute(
            """
            SELECT r.row_number, l.match_status
            FROM raw_record r JOIN record_entity_link l ON l.record_id = r.record_id
            WHERE r.batch_id = %s
            """,
            (batch_id,),
        )
        for n, status in cur:
            rows[n]["match_status"] = status

    return rows


def compare(left: dict, right: dict) -> list[str]:
    problems = []
    if set(left) != set(right):
        only_left = sorted(set(left) - set(right))[:5]
        only_right = sorted(set(right) - set(left))[:5]
        problems.append(f"different rows: batch-only {only_left}, record-only {only_right}")
        return problems

    for row_number in sorted(left):
        a, b = left[row_number], right[row_number]
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                problems.append(
                    f"row {row_number}: {key} batch={a.get(key)!r} record={b.get(key)!r}"
                )
    return problems


_SCOPED_ENTITIES = """
SELECT e.entity_id FROM entity e
JOIN raw_record r ON r.record_id = e.first_seen_record_id
JOIN batch b ON b.batch_id = r.batch_id
JOIN source s ON s.source_id = b.source_id
WHERE s.source_name = %s
"""


def cleanup(conn, source_names):
    """Strictly scoped to the parity sources, children before parents.

    Phased across all the named sources rather than finishing one source at a
    time: one source's record can be linked to another's entity — that is the
    cross-dataset linking working — so every link has to go before any entity
    does.
    """
    with conn.cursor() as cur:
        # Phase 1: everything hanging off a record. Not all of these carry a
        # batch_id, but they all point at a record.
        for name in source_names:
            for table in ("validation_result", "record_validation", "quarantine_event",
                          "quarantine_item", "match_candidate", "record_entity_link",
                          "attribute_observation"):
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

        # Phase 2: the entities those records created, and what hangs off them.
        for name in source_names:
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

        # Phase 3: the records themselves and the layout they came from.
        for name in source_names:
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


def main() -> int:
    path = Path("/tmp/_parity.csv")
    generate(path)
    print(f"generated {ROWS} rows\n")

    with connect() as conn:
        cleanup(conn, [BATCH_SOURCE, RECORD_SOURCE])

    # Each path gets the same starting world. Running them back to back against
    # a shared one would not compare them: the second would simply link to the
    # entities the first had just built — which is correct behaviour, and
    # exactly why it cannot be used as evidence that the two agree.
    batch_id, batch_seconds = run_batch_path(path)
    with connect() as conn:
        left = snapshot(conn, batch_id)
        cleanup(conn, [BATCH_SOURCE])

    started = time.perf_counter()
    record_result = run_file(
        path=path, entity_type="company", source_name=RECORD_SOURCE,
        source_type="csv", reliability=0.5, allow_reingest=True, publish=False,
    )
    record_seconds = time.perf_counter() - started

    with connect() as conn:
        right = snapshot(conn, record_result.batch_id)

    print(f"batch path      {batch_seconds:6.2f}s  ({ROWS / batch_seconds:,.0f} rec/s)")
    print(f"per-record path {record_seconds:6.2f}s  ({ROWS / record_seconds:,.0f} rec/s)")
    print(f"                {record_seconds / batch_seconds:.2f}x\n")

    problems = compare(left, right)
    if problems:
        print(f"DIVERGED in {len(problems)} place(s):")
        for line in problems[:40]:
            print(f"  {line}")
        return 1

    sample = left[1]
    print(f"IDENTICAL across {len(left)} rows")
    print(f"  compared per row: {', '.join(sorted(sample))}")
    print(f"  e.g. row 1: {sample['observations']} observations, "
          f"{sample['judgements']} judgements, status {sample['status']}")

    statuses = {}
    for row in left.values():
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
    print(f"  validation statuses: {statuses}")

    with connect() as conn:
        cleanup(conn, [RECORD_SOURCE])
    print("\nparity data removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
