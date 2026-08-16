"""Measure what it costs to make the record the unit of work.

The unit of work modelled here is one real record's write footprint: one
raw_record plus the ~10 attribute_observations that normalization derives from
it. That is the shape the fused per-record pipeline has to sustain, so it is the
shape worth measuring — an insert into a single table would flatter the result.

Each variant adds one optimization to the one above it, so the table says which
change bought what rather than only that the stack is faster.
"""

import os
import random
import secrets
import time
import uuid

import psycopg
from common.config import settings
from psycopg.types.json import Json

DSN = settings.postgres_dsn
ROWS = int(os.environ.get("BENCH_ROWS", "5000"))
OBS_PER_RECORD = 10


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7, generated client-side.

    Same layout Postgres' uuidv7() produces: 48-bit millisecond timestamp, then
    randomness. Generating it here rather than server-side is what lets a
    record's observations be written in the same round-trip as the record they
    belong to, instead of waiting for a RETURNING to come back first.
    """
    value = int.from_bytes(secrets.token_bytes(16), "big")
    value &= ~(0xFFFF << 112)
    value |= (time.time_ns() // 1_000_000) << 80
    value &= ~(0xF000 << 64)
    value |= 0x7000 << 64
    value &= ~(0xC000 << 48)
    value |= 0x8000 << 48
    return uuid.UUID(int=value)


def make_rows(n: int):
    rows = []
    for i in range(n):
        rows.append({
            "source_record_id": str(i + 1),
            "row_number": i + 1,
            "payload": {
                "full_name": f"Person {i}", "e_mail": f"p{i}@example.com",
                "mobile_no": f"+1 555 {i:04d}", "org_name": f"Org {i % 500}",
                "city": "Boston", "postal_code": "00501",
                "title": "Engineer", "website": f"https://org{i % 500}.com",
                "notes": "x" * 40, "customer_ref": f"{i:020d}",
            },
        })
    return rows


INSERT_RECORD = """
INSERT INTO raw_record (record_id, batch_id, source_id, entity_type,
                        source_record_id, row_number, payload_hash, raw_payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""
INSERT_RECORD_RETURNING = """
INSERT INTO raw_record (batch_id, source_id, entity_type,
                        source_record_id, row_number, payload_hash, raw_payload)
VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING record_id
"""
INSERT_OBS = """
INSERT INTO attribute_observation (observation_id, record_id, batch_id, source_id,
    entity_type, source_column, canonical_field, mapping_status, value_index,
    raw_value, normalized_value, value_type, normalization_method, is_null_token,
    observed_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
"""
INSERT_OBS_SERVER_ID = """
INSERT INTO attribute_observation (record_id, batch_id, source_id,
    entity_type, source_column, canonical_field, mapping_status, value_index,
    raw_value, normalized_value, value_type, normalization_method, is_null_token,
    observed_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
"""


def obs_params(record_id, batch_id, source_id, payload, with_id: bool):
    out = []
    for index, (column, value) in enumerate(list(payload.items())[:OBS_PER_RECORD]):
        tail = (
            record_id, batch_id, source_id, "person", column, column,
            "auto_accepted", 0, str(value), str(value).lower(), "text",
            "text:trim", False,
        )
        out.append(((uuid7(), *tail) if with_id else tail))
    return out


# --------------------------------------------------------------------------
# variants
# --------------------------------------------------------------------------

def v_batched(conn, batch_id, source_id, rows):
    """Today's architecture: executemany in 1,000-row chunks, commit per chunk."""
    with conn.cursor() as cur:
        for start in range(0, len(rows), 1000):
            chunk = rows[start:start + 1000]
            ids = [uuid7() for _ in chunk]
            cur.executemany(INSERT_RECORD, [
                (rid, batch_id, source_id, "person", r["source_record_id"],
                 r["row_number"], "h" * 64, Json(r["payload"]))
                for rid, r in zip(ids, chunk)
            ])
            obs = []
            for rid, r in zip(ids, chunk):
                obs.extend(obs_params(rid, batch_id, source_id, r["payload"], True))
            cur.executemany(INSERT_OBS, obs)
            conn.commit()


def v_naive(conn, batch_id, source_id, rows):
    """One record at a time, the obvious way: RETURNING for the id, then one
    INSERT per observation, then commit."""
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(INSERT_RECORD_RETURNING, (
                batch_id, source_id, "person", r["source_record_id"],
                r["row_number"], "h" * 64, Json(r["payload"])))
            record_id = cur.fetchone()[0]
            for params in obs_params(record_id, batch_id, source_id, r["payload"], False):
                cur.execute(INSERT_OBS_SERVER_ID, params)
            conn.commit()


def v_clientid(conn, batch_id, source_id, rows):
    """+ client-side UUIDv7 and one executemany for the record's observations.
    The record is still its own transaction; only the round-trips collapse."""
    with conn.cursor() as cur:
        for r in rows:
            rid = uuid7()
            cur.execute(INSERT_RECORD, (
                rid, batch_id, source_id, "person", r["source_record_id"],
                r["row_number"], "h" * 64, Json(r["payload"])))
            cur.executemany(
                INSERT_OBS, obs_params(rid, batch_id, source_id, r["payload"], True))
            conn.commit()


def v_pipeline(conn, batch_id, source_id, rows):
    """+ libpq pipeline mode, synced once per record.

    Syncing per record is the point: the statements for one record go out in a
    single round-trip, but the record is still the unit that succeeds or fails.
    """
    with conn.cursor() as cur:
        for r in rows:
            with conn.pipeline():
                rid = uuid7()
                cur.execute(INSERT_RECORD, (
                    rid, batch_id, source_id, "person", r["source_record_id"],
                    r["row_number"], "h" * 64, Json(r["payload"])))
                cur.executemany(
                    INSERT_OBS, obs_params(rid, batch_id, source_id, r["payload"], True))
                conn.commit()


def v_async_commit(conn, batch_id, source_id, rows):
    """+ synchronous_commit=off for this session only.

    Atomicity and isolation are unchanged; only the fsync at commit is deferred.
    A crash can lose the last fraction of a second of committed records, never
    corrupt or half-apply one.
    """
    with conn.cursor() as cur:
        cur.execute("SET LOCAL synchronous_commit = off")
        conn.commit()
        cur.execute("SET synchronous_commit = off")
        conn.commit()
        for r in rows:
            with conn.pipeline():
                rid = uuid7()
                cur.execute(INSERT_RECORD, (
                    rid, batch_id, source_id, "person", r["source_record_id"],
                    r["row_number"], "h" * 64, Json(r["payload"])))
                cur.executemany(
                    INSERT_OBS, obs_params(rid, batch_id, source_id, r["payload"], True))
                conn.commit()
        cur.execute("SET synchronous_commit = on")
        conn.commit()


VARIANTS = [
    ("batched (1000/txn)  [today]", v_batched),
    ("per-record, naive", v_naive),
    ("  + client uuid7 + executemany", v_clientid),
    ("  + pipeline mode", v_pipeline),
    ("  + synchronous_commit=off", v_async_commit),
]


def setup(conn, name):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO source (source_name, source_type, reliability) VALUES (%s,'csv',0.5) "
            "ON CONFLICT (source_name) DO UPDATE SET updated_at = now() RETURNING source_id",
            (name,))
        source_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO batch (source_id, entity_type, file_path, file_name, file_hash,"
            " file_size_bytes, status) VALUES (%s,'person','/bench','bench.csv',%s,0,'running')"
            " RETURNING batch_id",
            (source_id, secrets.token_hex(32)))
        batch_id = cur.fetchone()[0]
    conn.commit()
    return source_id, batch_id


def teardown(conn, source_id, batch_ids):
    """Scoped strictly to the batches this run created."""
    with conn.cursor() as cur:
        for batch_id in batch_ids:
            cur.execute("DELETE FROM attribute_observation WHERE batch_id = %s", (batch_id,))
            cur.execute("DELETE FROM raw_record WHERE batch_id = %s", (batch_id,))
            cur.execute("DELETE FROM batch WHERE batch_id = %s", (batch_id,))
        cur.execute("DELETE FROM source WHERE source_id = %s", (source_id,))
    conn.commit()


def main():
    random.seed(7)
    rows = make_rows(ROWS)
    results = []
    created = []
    source_id = None

    for label, fn in VARIANTS:
        with psycopg.connect(DSN) as conn:
            source_id, batch_id = setup(conn, "_bench_perrecord")
            created.append(batch_id)
            start = time.perf_counter()
            fn(conn, batch_id, source_id, rows)
            elapsed = time.perf_counter() - start
        results.append((label, elapsed))

    baseline = results[0][1]
    naive = results[1][1]
    print(f"\n{ROWS:,} records x {OBS_PER_RECORD} observations "
          f"= {ROWS * (OBS_PER_RECORD + 1):,} rows written per variant\n")
    print(f"{'variant':34}{'seconds':>10}{'rec/s':>12}{'vs batched':>13}{'vs naive':>11}")
    print("-" * 80)
    for label, elapsed in results:
        print(f"{label:34}{elapsed:10.2f}{ROWS / elapsed:12,.0f}"
              f"{elapsed / baseline:12.2f}x{naive / elapsed:10.2f}x")

    with psycopg.connect(DSN) as conn:
        teardown(conn, source_id, created)
    print("\nbenchmark rows removed")


if __name__ == "__main__":
    main()
