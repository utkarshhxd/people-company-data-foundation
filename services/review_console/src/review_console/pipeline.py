"""Read projections of pipeline progress: one batch, or everything.

A record's stage is not a column anywhere. It is inferred by which per-stage
tables hold a row for it -- `raw_record` (ingested), `attribute_observation`
(normalized), `record_validation` (validated), `record_entity_link` (resolved),
`golden_attribute` (golden built) -- because the record-at-a-time pipeline
(`docs/decisions/0012-record-at-a-time-processing.md`) carries a record through
all of them in one transaction. Nothing here writes, and nothing here changes
what a record's presence in one of those tables means; it only counts rows
already there, the same way `review.queue_summary()` counts the four review
queues.

`batch_id = NULL` means "everything ever loaded" -- the same query answers both
one batch's funnel and the global rollup, parameterized rather than duplicated,
the same optional-filter style `review._MAPPING_SQL` uses.
"""

from typing import Any

from common.db import connect
from psycopg.rows import dict_row

from review_console import stages


def _rows(conn, sql: str, params: dict | tuple = ()) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _one(conn, sql: str, params: dict | tuple = ()) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


# --------------------------------------------------------------------------
# recent batches
# --------------------------------------------------------------------------

_BATCHES_SQL = """
SELECT b.batch_id, b.status, s.source_name, b.file_name, b.entity_type,
       b.rows_read, b.rows_ingested, b.rows_skipped AS rows_failed,
       b.started_at, b.finished_at
FROM batch b
JOIN source s USING (source_id)
ORDER BY b.started_at DESC
LIMIT %(limit)s
"""


def recent_batches(limit: int = 50) -> list[dict[str, Any]]:
    """Batches, most recent first, with the counters the batch is writing live.

    `rows_read` is incremented once per record as the run commits it
    (`record_pipeline/runner.py`), so a batch still `status = 'running'` shows
    real progress, not a stale snapshot -- poll this while it runs.
    """
    with connect() as conn:
        rows = _rows(conn, _BATCHES_SQL, {"limit": limit})
    return [
        {
            "batch_id": str(row["batch_id"]),
            "status": row["status"],
            "source_name": row["source_name"],
            "file_name": row["file_name"],
            "entity_type": row["entity_type"],
            "rows_read": row["rows_read"],
            "rows_ingested": row["rows_ingested"],
            # The column is named rows_skipped; what it actually holds is the
            # count of rows that failed processing and landed in record_error.
            # See finish_batch() in ingestion.repository.
            "rows_failed": row["rows_failed"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }
        for row in rows
    ]


# --------------------------------------------------------------------------
# funnel: one batch, or every batch
# --------------------------------------------------------------------------

_FUNNEL_SQL = """
WITH val AS (
    SELECT
        count(*) FILTER (WHERE status = 'valid')   AS valid,
        count(*) FILTER (WHERE status = 'warning') AS warning,
        count(*) FILTER (WHERE status = 'invalid') AS invalid
    FROM record_validation
    WHERE %(batch_id)s::uuid IS NULL OR batch_id = %(batch_id)s::uuid
),
qtn AS (
    SELECT
        count(*) FILTER (WHERE status = 'open')     AS open,
        count(*) FILTER (WHERE status = 'released') AS released,
        count(*) FILTER (WHERE status = 'rejected') AS rejected,
        count(*) FILTER (WHERE status = 'resolved') AS resolved
    FROM quarantine_item
    WHERE %(batch_id)s::uuid IS NULL OR batch_id = %(batch_id)s::uuid
),
res AS (
    -- role = 'self': a person record also links to an employer entity
    -- (migration 0012), and record_entity_link is now (record_id, role)
    -- rather than (record_id) unique. The funnel counts each record once, by
    -- its own resolution -- the employer link is a different relationship,
    -- not a second stage this record itself passes through.
    SELECT
        count(*) FILTER (WHERE match_status = 'auto_linked') AS auto_linked,
        count(*) FILTER (WHERE match_status = 'new_entity')  AS new_entity,
        count(*) FILTER (WHERE match_status = 'manual')      AS manual
    FROM record_entity_link
    WHERE role = 'self'
      AND (%(batch_id)s::uuid IS NULL OR batch_id = %(batch_id)s::uuid)
),
gold AS (
    -- A record whose OWN entity has a current golden value for at least one
    -- field. Golden building is per-entity, not per-record, so this is
    -- "records whose entity's golden record exists", not a count of writes.
    SELECT count(DISTINCT l.record_id) AS built
    FROM record_entity_link l
    JOIN golden_attribute g ON g.entity_id = l.entity_id AND g.valid_to IS NULL
    WHERE l.role = 'self'
      AND (%(batch_id)s::uuid IS NULL OR l.batch_id = %(batch_id)s::uuid)
),
mapping AS (
    -- Mappings are keyed by layout, not by batch: a batch reusing an
    -- already-known layout is counted at the batch that FIRST saw it, so a
    -- later batch on a settled layout correctly shows zero here even though
    -- it depended on that earlier decision.
    SELECT count(*) AS needs_review
    FROM column_mapping cm
    JOIN source_schema ss USING (source_schema_id)
    WHERE cm.mapping_status = 'needs_review'
      AND (%(batch_id)s::uuid IS NULL OR ss.first_seen_batch_id = %(batch_id)s::uuid)
),
cand AS (
    SELECT count(*) AS open
    FROM match_candidate c
    JOIN raw_record r ON r.record_id = c.record_id
    WHERE c.status = 'open'
      AND (%(batch_id)s::uuid IS NULL OR r.batch_id = %(batch_id)s::uuid)
)
SELECT
    (SELECT count(*) FROM raw_record
      WHERE %(batch_id)s::uuid IS NULL OR batch_id = %(batch_id)s::uuid) AS ingested,
    (SELECT count(*) FROM record_error
      WHERE %(batch_id)s::uuid IS NULL OR batch_id = %(batch_id)s::uuid) AS failed,
    (SELECT count(DISTINCT record_id) FROM attribute_observation
      WHERE %(batch_id)s::uuid IS NULL OR batch_id = %(batch_id)s::uuid) AS normalized,
    val.valid AS validated_valid, val.warning AS validated_warning,
    val.invalid AS validated_invalid,
    qtn.open AS quarantine_open, qtn.released AS quarantine_released,
    qtn.rejected AS quarantine_rejected, qtn.resolved AS quarantine_resolved,
    res.auto_linked AS resolved_auto_linked, res.new_entity AS resolved_new_entity,
    res.manual AS resolved_manual,
    gold.built AS golden_built,
    mapping.needs_review AS mapping_needs_review,
    cand.open AS candidates_open
FROM val, qtn, res, gold, mapping, cand
"""


def _funnel(conn, batch_id: str | None) -> dict[str, Any]:
    row = _one(conn, _FUNNEL_SQL, {"batch_id": batch_id}) or {}

    def n(key: str) -> int:
        return int(row.get(key) or 0)

    return {
        "ingested": n("ingested"),
        "failed": n("failed"),
        "normalized": n("normalized"),
        "validated": {
            "valid": n("validated_valid"),
            "warning": n("validated_warning"),
            "invalid": n("validated_invalid"),
        },
        "quarantine": {
            "open": n("quarantine_open"),
            "released": n("quarantine_released"),
            "rejected": n("quarantine_rejected"),
            "resolved": n("quarantine_resolved"),
        },
        "resolved": {
            "auto_linked": n("resolved_auto_linked"),
            "new_entity": n("resolved_new_entity"),
            "manual": n("resolved_manual"),
        },
        "golden_built": n("golden_built"),
        "needs_review": {
            "mapping": n("mapping_needs_review"),
            "candidates": n("candidates_open"),
        },
    }


def global_summary() -> dict[str, Any]:
    """The funnel across every batch ever loaded."""
    with connect() as conn:
        return _funnel(conn, None)


_BATCH_SQL = """
SELECT b.batch_id, b.status, s.source_name, b.file_name, b.entity_type,
       b.rows_read, b.rows_ingested, b.rows_skipped AS rows_failed,
       b.started_at, b.finished_at, b.error_message
FROM batch b
JOIN source s USING (source_id)
WHERE b.batch_id = %(batch_id)s::uuid
"""


def batch_detail(batch_id: str) -> dict[str, Any] | None:
    """One batch: its own counters, plus how far its records got."""
    with connect() as conn:
        batch = _one(conn, _BATCH_SQL, {"batch_id": batch_id})
        if batch is None:
            return None
        funnel = _funnel(conn, batch_id)

    return {
        "batch_id": str(batch["batch_id"]),
        "status": batch["status"],
        "source_name": batch["source_name"],
        "file_name": batch["file_name"],
        "entity_type": batch["entity_type"],
        "rows_read": batch["rows_read"],
        "rows_ingested": batch["rows_ingested"],
        "rows_failed": batch["rows_failed"],
        "started_at": batch["started_at"],
        "finished_at": batch["finished_at"],
        "error_message": batch["error_message"],
        "funnel": funnel,
        # Whether a manual stage run is in flight for this batch right now,
        # and how the last one finished. Idle stages are None, not absent --
        # the UI needs to tell "never run" from "ran, don't have a key for it".
        "stages": stages.status_for(batch_id),
    }
