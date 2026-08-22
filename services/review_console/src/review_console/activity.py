"""One log, from the several places this system already writes what happened.

The question people actually ask a console is "what went on, and why did that
stop", and until now answering it meant four different places: the batch table
for loads, `record_error` for the rows that failed, `pipeline_control_event`
for who stopped what, and `docker compose logs <the right container>` for
everything a service said out loud -- which needs host access and a guess at
which of eleven containers to look in.

So they are read together, into one time-ordered feed with one shape. Not a
new store: every row here is a projection of something already durable, which
is why nothing is written by this module at all. `service_log` is the one
addition, and it is a bounded tail of what the services print anyway (see
`common.dblog`); container stdout remains the complete record.

Each entry carries a level, so "everything that happened" and "everything that
went wrong" are the same view with a filter, and a reference -- a batch, a
record, a stage -- so an entry is a starting point rather than a dead end.
"""

import logging
from typing import Any

from common.db import connect
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

# 'error' means somebody has to do something; 'warn' means somebody should
# look; 'info' is the ordinary progress of the system. Mapped onto each source
# rather than stored, because the sources disagree about what a level is and
# only one of them (`service_log`) has one at all.
LEVELS = ("error", "warn", "info")
_RANK = {"error": 3, "warn": 2, "info": 1}

KINDS = ("service", "control", "load", "queue", "record")

# Ceilings, not page sizes: this is a tail, and asking for a million rows of it
# is a way to hurt the database rather than a way to learn anything.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

# Each branch is ordered and capped on its own before the union, so no branch
# can scan its whole table just to be discarded by the outer LIMIT. Every one
# of them has an index on the column it orders by.
_FEED_SQL = """
WITH service AS (
    SELECT created_at AS at,
           'service'::text AS kind,
           CASE WHEN level_no >= 40 THEN 'error' ELSE 'warn' END AS level,
           service AS actor,
           message AS title,
           logger AS context,
           NULL::text AS ref_kind,
           NULL::text AS ref_id,
           detail
    FROM service_log
    ORDER BY created_at DESC
    LIMIT %(limit)s
), control AS (
    SELECT created_at AS at,
           'control'::text AS kind,
           CASE WHEN to_state = 'paused' THEN 'error' ELSE 'info' END AS level,
           stage AS actor,
           CASE WHEN to_state = 'paused'
                THEN stage || ' was stopped: ' || coalesce(reason, 'no reason given')
                ELSE stage || ' was started again'
           END AS title,
           'by ' || changed_by || coalesce(' -- ' || note, '') AS context,
           'stage'::text AS ref_kind,
           stage AS ref_id,
           evidence AS detail
    FROM pipeline_control_event
    ORDER BY created_at DESC
    LIMIT %(limit)s
), load AS (
    SELECT coalesce(b.finished_at, b.started_at) AS at,
           'load'::text AS kind,
           CASE b.status WHEN 'failed' THEN 'error'
                         WHEN 'running' THEN 'info'
                         ELSE 'info' END AS level,
           s.source_name AS actor,
           CASE b.status
                WHEN 'failed' THEN b.file_name || ' failed to load: '
                                   || coalesce(b.error_message, 'no reason recorded')
                WHEN 'running' THEN b.file_name || ' is loading'
                ELSE b.file_name || ' loaded ' || b.rows_ingested || ' row(s)'
           END AS title,
           b.entity_type || ' -- read ' || b.rows_read
               || ', skipped ' || b.rows_skipped AS context,
           'batch'::text AS ref_kind,
           b.batch_id::text AS ref_id,
           '{}'::jsonb AS detail
    FROM batch b JOIN source s ON s.source_id = b.source_id
    ORDER BY coalesce(b.finished_at, b.started_at) DESC
    LIMIT %(limit)s
), queue AS (
    SELECT coalesce(finished_at, started_at, queued_at) AS at,
           'queue'::text AS kind,
           CASE status WHEN 'failed' THEN 'error'
                       WHEN 'held' THEN 'warn'
                       ELSE 'info' END AS level,
           source_name AS actor,
           CASE status
                WHEN 'failed' THEN file_name || ' could not be queued through: '
                                   || coalesce(error, 'no reason recorded')
                WHEN 'held' THEN file_name || ' is held -- ingestion is stopped'
                WHEN 'cancelled' THEN file_name || ' was cancelled'
                WHEN 'running' THEN file_name || ' is being loaded'
                WHEN 'completed' THEN file_name || ' finished loading'
                ELSE file_name || ' was queued'
           END AS title,
           'queued by ' || queued_by AS context,
           'queue'::text AS ref_kind,
           queue_id::text AS ref_id,
           detail
    FROM ingest_queue
    ORDER BY queued_at DESC
    LIMIT %(limit)s
), record AS (
    SELECT e.occurred_at AS at,
           'record'::text AS kind,
           CASE WHEN e.status = 'open' THEN 'warn' ELSE 'info' END AS level,
           e.stage AS actor,
           'row ' || e.row_number || ' of ' || b.file_name || ' failed in '
               || e.stage || ': ' || e.error_type AS title,
           e.error_message AS context,
           'record'::text AS ref_kind,
           e.error_id::text AS ref_id,
           jsonb_build_object('status', e.status, 'batch_id', e.batch_id) AS detail
    FROM record_error e JOIN batch b ON b.batch_id = e.batch_id
    ORDER BY e.occurred_at DESC
    LIMIT %(limit)s
)
SELECT * FROM (
    SELECT * FROM service
    UNION ALL SELECT * FROM control
    UNION ALL SELECT * FROM load
    UNION ALL SELECT * FROM queue
    UNION ALL SELECT * FROM record
) merged
WHERE at IS NOT NULL
  AND (%(kind)s::text IS NULL OR kind = %(kind)s)
  AND (%(min_rank)s::int IS NULL
       OR CASE level WHEN 'error' THEN 3 WHEN 'warn' THEN 2 ELSE 1 END >= %(min_rank)s)
ORDER BY at DESC
LIMIT %(limit)s
"""


def feed(
    kind: str | None = None,
    level: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """The most recent things that happened, newest first.

    `level` is a floor, not an equality: asking for warnings gets errors too,
    because nobody wanting to know what went wrong wants the worst of it
    filtered out.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    params = {
        "limit": limit,
        "kind": kind,
        "min_rank": _RANK.get(level or "") or None,
    }
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_FEED_SQL, params)
        rows = cur.fetchall()

    counts: dict[str, int] = {level_name: 0 for level_name in LEVELS}
    for row in rows:
        counts[row["level"]] = counts.get(row["level"], 0) + 1
    return {
        "entries": rows,
        "count": len(rows),
        "levels": counts,
        # So a page can say "showing the most recent 100 of possibly more"
        # rather than implying this is everything there has ever been.
        "truncated": len(rows) >= limit,
    }


_SERVICE_LOG_SQL = """
SELECT service, level, level_no, logger, message, detail, created_at
FROM service_log
WHERE (%(service)s::text IS NULL OR service = %(service)s)
  AND level_no >= %(min_level)s
ORDER BY created_at DESC
LIMIT %(limit)s
"""


def service_log(
    service: str | None = None,
    min_level: int = logging.WARNING,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Just what the services said, unmixed with what the data did.

    Kept separate from `feed` because they answer different questions: the feed
    is what happened to the work, this is what the processes doing the work
    complained about, and reading one while looking for the other is how an
    incident takes an extra twenty minutes.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            _SERVICE_LOG_SQL,
            {"service": service, "min_level": min_level, "limit": limit},
        )
        rows = cur.fetchall()
        cur.execute(
            """
            SELECT service, count(*) AS n,
                   count(*) FILTER (WHERE level_no >= 40) AS errors,
                   max(created_at) AS latest
            FROM service_log
            WHERE created_at > now() - interval '24 hours'
            GROUP BY service ORDER BY service
            """
        )
        by_service = cur.fetchall()
    return {
        "entries": rows,
        "count": len(rows),
        "by_service": by_service,
        "truncated": len(rows) >= limit,
    }
