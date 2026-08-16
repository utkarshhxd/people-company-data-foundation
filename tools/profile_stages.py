"""Read-only profile: where does a record's time actually go?

Writes nothing. Runs the per-record compute stages against rows built in memory,
then times the read queries golden performs per record against whatever entities
already exist. The point is to separate CPU that batching cannot help from
round-trips that it can.
"""

import time
from datetime import datetime, timezone

from common.canonical import CANONICAL_SCHEMA_VERSION  # noqa: F401
from common.db import connect
from common.ids import uuid7
from mapping.engine import map_columns
from normalization.pipeline import observations_for_record
from resolution.keys import keys_for
from validation.pipeline import validate_record

COLUMNS = ["company_name", "email", "phone", "website", "street_address",
           "city", "postal_code", "employee_count", "founded_year", "description"]
ROWS = 5_000


def make_rows(n):
    out = []
    for i in range(1, n + 1):
        out.append({
            "company_name": f"Org {i}",
            "email": f"info{i}@org{i}.com" if i % 7 else "N/A",
            "phone": "0000000000" if i % 31 == 0 else f"+1 555 {i:05d}",
            "website": f"https://org{i}.com" if i % 5 else "",
            "street_address": (f"{i}WisconsinAveNW" if i % 53 == 0
                               else f"{i} Wisconsin Ave NW"),
            "city": f"City {i % 200}",
            "postal_code": "00501" if i % 3 == 0 else f"{10000 + (i % 80000)}",
            "employee_count": str((i % 900) + 1),
            "founded_year": str(1950 + (i % 70)),
            "description": f"desc {i}",
        })
    return out


def main():
    rows = make_rows(ROWS)
    samples = {c: [str(r[c]) for r in rows[:200]] for c in COLUMNS}

    t0 = time.perf_counter()
    decided = map_columns(COLUMNS, "company", samples)
    map_time = time.perf_counter() - t0

    mappings = {
        d.source_column: {"canonical_field": d.canonical_field,
                          "mapping_status": "auto_accepted"}
        for d in decided
    }

    batch_id, source_id = str(uuid7()), str(uuid7())
    observed_at = datetime.now(timezone.utc)

    # --- normalize -------------------------------------------------------
    per_record_obs = []
    t0 = time.perf_counter()
    for row in rows:
        rid = uuid7()
        per_record_obs.append((rid, observations_for_record(
            rid, row, mappings, "company", observed_at, batch_id, source_id)))
    norm_time = time.perf_counter() - t0

    # shape them the way unit.py does
    t0 = time.perf_counter()
    shaped = []
    for rid, tuples in per_record_obs:
        obs = []
        for r in tuples:
            obs.append({
                "observation_id": uuid7(), "record_id": r[0], "source_column": r[4],
                "canonical_field": r[5], "value_index": r[7], "raw_value": r[8],
                "normalized_value": r[9], "value_type": r[10],
                "normalization_method": r[11], "is_null_token": r[12],
            })
        shaped.append((rid, obs))
    shape_time = time.perf_counter() - t0

    # --- validate --------------------------------------------------------
    t0 = time.perf_counter()
    verdicts = [validate_record(rid, obs, "company", batch_id, source_id)
                for rid, obs in shaped]
    val_time = time.perf_counter() - t0

    # --- identity keys ---------------------------------------------------
    t0 = time.perf_counter()
    total_keys = 0
    for rid, obs in shaped:
        values = {}
        for o in sorted(obs, key=lambda o: (o["source_column"], o["value_index"])):
            if o["canonical_field"] and o["normalized_value"]:
                values.setdefault(o["canonical_field"], []).append(o["normalized_value"])
        total_keys += len(keys_for("company", values, source_id, role_email=False))
    keys_time = time.perf_counter() - t0

    obs_count = sum(len(o) for _, o in shaped)
    judgements = sum(len(v.result_rows) for v in verdicts)
    compute = norm_time + shape_time + val_time + keys_time

    print(f"\n{ROWS:,} records, {obs_count:,} observations, {judgements:,} judgements")
    print(f"{'phase':28}{'seconds':>10}{'us/record':>12}{'% compute':>11}")
    print("-" * 61)
    for label, t in [("map_columns (once)", map_time), ("normalize", norm_time),
                     ("shape observations", shape_time), ("validate", val_time),
                     ("identity keys", keys_time)]:
        share = "" if label.endswith("(once)") else f"{100 * t / compute:10.1f}%"
        print(f"{label:28}{t:10.3f}{1e6 * t / ROWS:12.1f}{share:>11}")
    print("-" * 61)
    print(f"{'CPU total (no database)':28}{compute:10.3f}{1e6 * compute / ROWS:12.1f}")
    print(f"\nCPU-only ceiling: {ROWS / compute:,.0f} records/s")

    # --- golden's per-record read cost, measured read-only ---------------
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT l.entity_id, count(*) AS records
                FROM record_entity_link l
                GROUP BY 1 ORDER BY 2 DESC LIMIT 1
            """)
            row = cur.fetchone()
        if row is None:
            print("\nno entities in the database; skipping golden read timing")
            return
        entity_id, linked_records = str(row[0]), row[1]

        from golden import repository as grepo
        t0 = time.perf_counter()
        obs = grepo.observations_for_entity(conn, entity_id)
        t_obs = time.perf_counter() - t0
        t0 = time.perf_counter()
        cur_vals = grepo.current_values(conn, entity_id)
        t_cur = time.perf_counter() - t0

        print(f"\ngolden read cost for the most-observed entity "
              f"({linked_records} linked record(s)):")
        print(f"  observations_for_entity  {1e3 * t_obs:8.2f} ms  "
              f"{sum(len(v) for v in obs.values())} observation(s), "
              f"{len(obs)} field(s)")
        print(f"  current_values           {1e3 * t_cur:8.2f} ms  "
              f"{len(cur_vals)} current value(s)")
        print(f"  -> rebuilt once per record linked to this entity")
        conn.rollback()


if __name__ == "__main__":
    main()
