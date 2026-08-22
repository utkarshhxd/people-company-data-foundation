"""A queue of files waiting to be loaded, and the one worker that loads them.

Dropping a file used to start ingesting it immediately, in a thread, tracked
in a dictionary. Dropping ten started ten of those at once. Nothing about that
is safe at ten: ten ingests interleave their batches, hold ten connections,
and compete for the same tables -- and a restart in the middle loses every
record of what was asked for, leaving bytes on disk with nothing saying what
they were meant to be loaded as.

Here the *request* is written down first, as a row, and work happens after.
That buys four things, all of which are the point:

  * **Order.** One item at a time, oldest first. A ten-file drop is a queue,
    not a stampede.
  * **Survival.** The console can restart mid-load and the queue is still
    there. An item whose worker died is picked up again rather than sitting
    'running' forever with its file silently never loaded.
  * **Visibility.** Everyone looking at the console sees the same queue, in
    the same order, with the same reasons attached -- not one browser's
    private view of its own uploads.
  * **A place for the pause to land.** When ingestion is stopped, items are
    marked `held` rather than failed. Holding says why, and costs nothing;
    failing would mean re-uploading files that were never wrong.

Exactly one worker runs across the whole deployment. It is not a pool: the
serialisation *is* the feature, and a second worker would put the interleaving
back.
"""

import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from common import control
from common.db import connect
from ingestion.pipeline import ReingestBlocked, ingest
from ingestion.readers import MultipleSheets, sheet_names
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

# Module-level so tests can substitute an instant fake without a database or a
# filesystem, the same way `uploads._INGEST` already worked.
_INGEST = ingest

# One claimer at a time, enforced by Postgres rather than by hoping only one
# console is running. Held for the length of the claim transaction only --
# milliseconds -- never for the length of an ingest.
CLAIM_LOCK = 0x9CDF10AD

POLL_SECONDS = 2.0

# A running item whose worker has not said anything for this long is treated as
# abandoned and queued again. Comfortably longer than the heartbeat interval,
# because requeueing an item that is genuinely still loading would ingest the
# same file twice -- ingestion's content hash refuses the duplicate, so the
# cost is a confusing error rather than doubled data, but it is still wrong.
HEARTBEAT_SECONDS = 20.0
ABANDONED_AFTER_SECONDS = 180.0

WAITING = ("queued", "held")
FINISHED = ("completed", "failed", "cancelled")

_COLUMNS = """
    queue_id, file_name, stored_path, size_bytes, entity_type, source_name,
    source_type, record_id_column, reliability, describes, batch_size,
    allow_reingest, sheet, status, queued_by, queued_at, started_at,
    finished_at, heartbeat_at, batch_id, rows_read, rows_ingested,
    rows_skipped, error, detail, attempts
"""


def _row(record: dict[str, Any]) -> dict[str, Any]:
    """One row, with the types a browser can actually use.

    `reliability` is `numeric`, which psycopg hands back as `Decimal` and
    `json` refuses outright; ids are UUIDs. Converted here rather than in the
    route so every caller gets the same shape.
    """
    out = dict(record)
    out["queue_id"] = str(out["queue_id"])
    if out.get("batch_id") is not None:
        out["batch_id"] = str(out["batch_id"])
    if out.get("reliability") is not None:
        out["reliability"] = float(out["reliability"])
    return out


# --------------------------------------------------------------------------
# writing and reading the queue
# --------------------------------------------------------------------------


def enqueue(
    *,
    file_name: str,
    stored_path: Path | str,
    size_bytes: int,
    entity_type: str,
    source_name: str,
    source_type: str,
    queued_by: str,
    batch_size: int,
    record_id_column: str | None = None,
    reliability: float = 0.5,
    describes: str | None = None,
    allow_reingest: bool = False,
    sheet: str | None = None,
) -> dict[str, Any]:
    """Write the request down. Returns the row, which is now the queue's copy.

    A file dropped while ingestion is stopped is written as `held` rather than
    `queued`. The worker would hold it on its next sweep anyway, but for those
    two seconds the console would show it as queued with nothing coming for it,
    and "waiting its turn" and "waiting for somebody to start ingestion again"
    are different things to be told.
    """
    with connect() as conn:
        status = "held" if control.get(conn, "ingestion").paused else "queued"
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                INSERT INTO ingest_queue (
                    file_name, stored_path, size_bytes, entity_type, source_name,
                    source_type, record_id_column, reliability, describes,
                    batch_size, allow_reingest, sheet, queued_by, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_COLUMNS}
                """,
                (file_name, str(stored_path), size_bytes, entity_type, source_name,
                 source_type, record_id_column, reliability, describes, batch_size,
                 allow_reingest, sheet, queued_by, status),
            )
            row = cur.fetchone()
        conn.commit()
    logger.info("queued %s as %s for %s", file_name, row["queue_id"], source_name)
    return _row(row)


def get(queue_id: str) -> dict[str, Any] | None:
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM ingest_queue WHERE queue_id = %s", (queue_id,)
        )
        row = cur.fetchone()
    return _row(row) if row else None


def listing(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    """The queue, newest first, with the waiting ones counted separately.

    Waiting is the number that matters operationally -- how much is still to
    come -- and it is not the length of what is shown, because the list is
    capped and includes finished items.
    """
    clause = "WHERE status = %s" if status else ""
    params: list[Any] = [status] if status else []
    params.append(limit)
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT {_COLUMNS} FROM ingest_queue
            {clause}
            ORDER BY queued_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = [_row(r) for r in cur.fetchall()]
        cur.execute(
            """
            SELECT status, count(*) AS n, coalesce(sum(size_bytes), 0) AS bytes
            FROM ingest_queue GROUP BY status
            """
        )
        totals = {r["status"]: {"count": r["n"], "bytes": int(r["bytes"])}
                  for r in cur.fetchall()}

    return {
        "items": rows,
        "count": len(rows),
        "totals": totals,
        "waiting": sum(totals.get(s, {}).get("count", 0) for s in WAITING),
        "waiting_bytes": sum(totals.get(s, {}).get("bytes", 0) for s in WAITING),
        "running": totals.get("running", {}).get("count", 0),
    }


class NotWaiting(Exception):
    """The item is no longer in a state the requested change applies to."""


def cancel(queue_id: str, reviewed_by: str) -> dict[str, Any]:
    """Take an item out of the queue before it starts.

    Only ever a waiting item. A running ingest is not cancelled from here:
    stopping one halfway would leave a partial batch, and the batch is what
    downstream reads -- `process abandon` is the tool for that, and it is
    deliberately a separate, deliberate act.
    """
    with connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                UPDATE ingest_queue
                SET status = 'cancelled', finished_at = now(), error = %s
                WHERE queue_id = %s AND status = ANY(%s)
                RETURNING {_COLUMNS}
                """,
                (f"cancelled by {reviewed_by}", queue_id, list(WAITING)),
            )
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise NotWaiting(
            f"{queue_id} is not waiting; only a queued or held item can be cancelled"
        )
    return _row(row)


def requeue(queue_id: str, reviewed_by: str) -> dict[str, Any]:
    """Put a finished item back in the queue, at the back.

    For the failed ones, which is most of why this exists: a load that failed
    because a source name was wrong or a stage was paused should be one click
    to try again, not a re-upload of a file that is already on disk. The
    previous error is cleared but the attempt count is not -- an item that has
    failed four times should say so.
    """
    with connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                UPDATE ingest_queue
                SET status = 'queued', error = NULL, started_at = NULL,
                    finished_at = NULL, heartbeat_at = NULL, queued_at = now(),
                    queued_by = %s
                WHERE queue_id = %s AND status = ANY(%s)
                RETURNING {_COLUMNS}
                """,
                (reviewed_by, queue_id, list(FINISHED)),
            )
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise NotWaiting(
            f"{queue_id} is not finished; only a completed, failed or cancelled "
            "item can be queued again"
        )
    if not Path(row["stored_path"]).is_file():
        # Said now rather than at the front of the queue in two minutes.
        logger.warning(
            "requeued %s but %s is gone; it will fail when it is reached",
            queue_id, row["stored_path"],
        )
    return _row(row)


# --------------------------------------------------------------------------
# the worker
# --------------------------------------------------------------------------


def _release_abandoned(conn) -> int:
    """Queue again anything whose worker stopped saying it was alive."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingest_queue
            SET status = 'queued', started_at = NULL, heartbeat_at = NULL,
                error = 'the worker loading this stopped; queued again'
            WHERE status = 'running'
              AND (heartbeat_at IS NULL
                   OR heartbeat_at < now() - make_interval(secs => %s))
            """,
            (ABANDONED_AFTER_SECONDS,),
        )
        return cur.rowcount


def _set_waiting_state(conn, to_state: str) -> int:
    """Move every waiting item between 'queued' and 'held'.

    Held is what the queue looks like when ingestion is paused. Written onto
    the items themselves so the reason is visible where somebody is already
    looking, instead of only in a banner they have to connect to the queue
    below it.
    """
    other = "held" if to_state == "queued" else "queued"
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ingest_queue SET status = %s WHERE status = %s",
            (to_state, other),
        )
        return cur.rowcount


def _claim(conn) -> dict[str, Any] | None:
    """Take the oldest queued item, or nothing.

    The advisory lock makes two workers impossible rather than unlikely, and
    it is a transaction lock: it is gone by the time ingest() starts, so a long
    load never blocks anyone from looking at the queue.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (CLAIM_LOCK,))
        cur.execute(
            f"""
            UPDATE ingest_queue SET
                status = 'running', started_at = now(), heartbeat_at = now(),
                attempts = attempts + 1, error = NULL
            WHERE queue_id = (
                SELECT queue_id FROM ingest_queue
                WHERE status = 'queued'
                ORDER BY queued_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING {_COLUMNS}
            """
        )
        row = cur.fetchone()
    conn.commit()
    return _row(row) if row else None


def _finish(queue_id: str, status: str, **fields: Any) -> None:
    sets = "".join(f", {name} = %s" for name in fields)
    params = [status, *fields.values(), queue_id]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE ingest_queue SET status = %s, finished_at = now(), "
                f"heartbeat_at = NULL{sets} WHERE queue_id = %s",
                params,
            )
        conn.commit()


class _Ticker:
    """Says the worker is still inside ingest(), until it isn't.

    A separate thread because ingest() is one synchronous call that can run for
    an hour on a large file, and the row has to keep saying it is alive for the
    whole of it or the abandonment sweep will take it back.
    """

    def __init__(self, queue_id: str) -> None:
        self._queue_id = queue_id
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"ingest-beat-{queue_id[:8]}", daemon=True
        )

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                with connect(autocommit=True) as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE ingest_queue SET heartbeat_at = now() "
                        "WHERE queue_id = %s AND status = 'running'",
                        (self._queue_id,),
                    )
            except Exception as exc:
                # Never fatal: a load that cannot say it is alive is still a
                # load doing its job. Worst case it is queued again afterwards
                # and ingestion's content hash refuses the duplicate.
                logger.warning("could not beat for queue item %s: %s",
                               self._queue_id, exc)


def _process(item: dict[str, Any]) -> None:
    """Run one queued item to a conclusion, whatever the conclusion is."""
    queue_id = item["queue_id"]
    path = Path(item["stored_path"])
    logger.info("loading %s (%s) for %s", item["file_name"], queue_id,
                item["source_name"])
    try:
        with _Ticker(queue_id):
            result = _INGEST(
                path=path,
                entity_type=item["entity_type"],
                source_name=item["source_name"],
                source_type=item["source_type"],
                reliability=item["reliability"],
                record_id_column=item["record_id_column"],
                allow_reingest=item["allow_reingest"],
                batch_size=item["batch_size"],
                describes=item["describes"],
                sheet=item["sheet"],
            )
    except control.StagePaused as exc:
        # Somebody stopped ingestion between the claim and the first row. Not a
        # failure of this file: put it back where it was.
        logger.warning("ingestion paused mid-claim, holding %s: %s", queue_id, exc)
        _finish(queue_id, "held", error=str(exc), started_at=None)
        return
    except MultipleSheets as exc:
        # The message names the sheets; this hands them back structured too, so
        # the console can offer them as picks instead of asking somebody to
        # retype a name they just read out of an error string.
        sheets: list[str] = []
        try:
            sheets = sheet_names(path)
        except Exception as list_exc:
            logger.warning("could not list sheets in %s: %s", path, list_exc)
        _finish(queue_id, "failed", error=str(exc),
                detail=json.dumps({"available_sheets": sheets}))
        return
    except ReingestBlocked as exc:
        _finish(queue_id, "failed", error=str(exc))
        return
    except Exception as exc:
        logger.warning("queue item %s (%s) failed: %s", queue_id,
                       item["file_name"], exc)
        _finish(queue_id, "failed", error=str(exc))
        return

    _finish(
        queue_id, "completed", batch_id=result.batch_id,
        rows_read=result.rows_read, rows_ingested=result.rows_ingested,
        rows_skipped=result.rows_skipped, error=None,
    )
    logger.info("loaded %s as batch %s (%d rows)", item["file_name"],
                result.batch_id, result.rows_ingested)


class Worker:
    """The single loop that turns queued rows into batches."""

    def __init__(self, poll_seconds: float = POLL_SECONDS) -> None:
        self._poll = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._was_paused: bool | None = None
        self.last_error: str | None = None
        self.last_pass_at: datetime | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="ingest-queue-worker", daemon=True
        )
        self._thread.start()
        logger.info("ingest queue worker started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _sweep(self) -> dict[str, Any] | None:
        """One pass: tidy up, decide whether to work, claim if so.

        The waiting items are reconciled on every pass rather than only when
        the pause changes. Doing it on the transition alone was wrong in the
        case that matters most: a file dropped *during* a pause arrives after
        the transition has been handled, so it sat as `queued` with nothing
        coming for it and nothing saying why. The update is a partial-indexed
        statement that normally matches no rows, which is a cheaper thing to be
        wrong about than the queue lying.
        """
        with connect() as conn:
            released = _release_abandoned(conn)
            if released:
                logger.warning("queued %d abandoned ingest item(s) again", released)

            paused = control.get(conn, "ingestion").paused
            moved = _set_waiting_state(conn, "held" if paused else "queued")
            if moved:
                # Logged only when something actually moved, or this is a line
                # per poll drowning out anything real.
                logger.warning(
                    "ingestion is %s: %d file(s) %s",
                    "stopped" if paused else "running", moved,
                    "held" if paused else "released",
                )
            self._was_paused = paused
            conn.commit()
            if paused:
                return None
            return _claim(conn)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._sweep()
                self.last_pass_at = datetime.now(UTC)
                self.last_error = None
            except Exception as exc:
                # Almost always Postgres being unreachable. Logged once per
                # pass and retried; the queue is in the database, so there is
                # nothing to lose by waiting and nothing to do but wait.
                self.last_error = str(exc)
                logger.warning("ingest queue worker could not poll: %s", exc)
                self._stop.wait(self._poll)
                continue

            if item is None:
                self._stop.wait(self._poll)
                continue
            try:
                _process(item)
            except Exception:
                # _process handles its own failures; anything reaching here is
                # the failure handling itself failing, which would leave the
                # item 'running'. The abandonment sweep picks it up.
                logger.exception("ingest queue worker crashed on an item")
                time.sleep(self._poll)


worker = Worker()
