"""The warnings and errors, into Postgres, without ever getting in the way.

Every service already prints what went wrong. The problem is only where it
lands: one container's stdout, readable by whoever is on the host and knows
which of eleven containers to look in. The console -- the thing an operator
actually has open -- could show what happened to the *records* and nothing at
all about what the *processes* said, so "why did that stop" ended at a shell.

So WARNING and above is copied into `service_log` as well. Three rules make
that safe to attach to the root logger of a service that must not stop:

**It never blocks the caller.** `emit` puts a row on a bounded queue and
returns. A background thread does the inserting. If Postgres is slow, the
queue fills; if it fills, rows are dropped and counted, because a service that
stalls on its own logging is worse than one whose log has a gap -- and the
gap is visible, since the drop count is reported the next time a write works.

**It never recurses.** The writer thread's own logging is not captured. Without
that, one failed insert logs a warning, which becomes a row, which fails to
insert, which logs a warning.

**It never grows without bound.** Postgres is not a log store. The table is
trimmed to a fixed number of rows, so a service stuck in a warning loop costs
a bounded amount of disk rather than a page.

Container stdout is unaffected and remains the complete record. This is a tail
of it, in the place people are already looking.
"""

import logging
import queue
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Small on purpose. This is a tail for a console, not a shipping buffer: if
# more than this is outstanding, the database is not keeping up and the honest
# outcome is to drop and say so rather than to accumulate.
MAX_PENDING = 2_000

# How long the writer waits for more rows before inserting what it has. Short
# enough that a warning appears in the console while somebody is still looking
# at the thing that caused it.
FLUSH_SECONDS = 1.0
MAX_BATCH = 100

# The ceiling on the table. Roughly a week of a healthy stack's warnings, and
# about twenty minutes of a badly broken one -- which is the case that matters,
# since that is when the ceiling does anything at all.
DEFAULT_MAX_ROWS = 20_000
TRIM_EVERY_SECONDS = 300.0

_INSERT = """
INSERT INTO service_log (service, level, level_no, logger, message, detail)
VALUES (%s, %s, %s, %s, %s, %s)
"""

_TRIM = """
DELETE FROM service_log
WHERE log_id <= (
    SELECT max(log_id) - %s FROM service_log
)
"""


class DatabaseHandler(logging.Handler):
    """Copies records at this handler's level into `service_log`."""

    def __init__(self, service: str, level: int = logging.WARNING,
                 max_rows: int = DEFAULT_MAX_ROWS) -> None:
        super().__init__(level)
        self.service = service
        self.max_rows = max_rows
        self._queue: queue.Queue[tuple[Any, ...]] = queue.Queue(maxsize=MAX_PENDING)
        self._stop = threading.Event()
        self._dropped = 0
        self._last_trim = 0.0
        self._thread = threading.Thread(
            target=self._run, name=f"dblog-{service}", daemon=True
        )
        self._thread.start()

    # -- the logging side, which must be cheap and must never raise ---------

    def emit(self, record: logging.LogRecord) -> None:
        if threading.current_thread() is self._thread:
            # The writer complaining about the write. Printed by the stream
            # handler like anything else; not turned into another row to fail
            # on.
            return
        try:
            row = (
                self.service,
                record.levelname,
                record.levelno,
                record.name,
                self.format(record),
                self._detail(record),
            )
            self._queue.put_nowait(row)
        except queue.Full:
            self._dropped += 1
        except Exception:  # pragma: no cover - defensive
            self.handleError(record)

    def _detail(self, record: logging.LogRecord) -> str:
        import json

        detail: dict[str, Any] = {
            "module": record.module,
            "line": record.lineno,
            "thread": record.threadName,
        }
        if record.exc_info:
            # Whatever the formatter already produced, rather than formatting
            # the traceback a second time.
            detail["traceback"] = (
                record.exc_text or logging.Formatter().formatException(record.exc_info)
            )
        return json.dumps(detail)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)
        super().close()

    # -- the writing side, which may block and may fail ---------------------

    def _drain(self) -> list[tuple[Any, ...]]:
        """Whatever is waiting, up to a batch, having waited briefly for more."""
        rows: list[tuple[Any, ...]] = []
        try:
            rows.append(self._queue.get(timeout=FLUSH_SECONDS))
        except queue.Empty:
            return rows
        while len(rows) < MAX_BATCH:
            try:
                rows.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return rows

    def _write(self, rows: list[tuple[Any, ...]]) -> None:
        from common.db import connect

        dropped, self._dropped = self._dropped, 0
        if dropped:
            # Reported as a row of its own, so a gap in the log says it is a
            # gap instead of looking like a quiet period.
            rows.append((
                self.service, "WARNING", logging.WARNING, __name__,
                (f"{dropped} log line(s) were dropped: the database was "
                 f"not keeping up with this service's warnings"),
                "{}",
            ))
        with connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.executemany(_INSERT, rows)
            now = time.monotonic()
            if now - self._last_trim > TRIM_EVERY_SECONDS:
                cur.execute(_TRIM, (self.max_rows,))
                self._last_trim = now

    def _run(self) -> None:
        while not self._stop.is_set():
            rows = self._drain()
            if not rows:
                continue
            try:
                self._write(rows)
            except Exception as exc:
                # Nothing to do but say so on stderr and carry on. Re-queueing
                # would turn an unreachable database into unbounded memory
                # growth, which is the failure this handler must not cause.
                self._dropped += len(rows)
                logger.warning("could not write %d log row(s): %s", len(rows), exc)
                self._stop.wait(FLUSH_SECONDS * 5)


_installed: DatabaseHandler | None = None


def install(service: str, level: int = logging.WARNING,
            max_rows: int = DEFAULT_MAX_ROWS) -> DatabaseHandler | None:
    """Attach one handler to the root logger. Safe to call more than once.

    Returns None when it is switched off, so a caller can tell the difference
    between "not installed" and "installed and quiet".
    """
    global _installed
    if _installed is not None:
        # `logging.basicConfig(force=True)` drops every root handler, and
        # `common.logging.configure` calls it. Re-attaching rather than
        # building a second handler keeps one queue and one writer thread.
        root = logging.getLogger()
        if _installed not in root.handlers:
            root.addHandler(_installed)
        return _installed
    handler = DatabaseHandler(service, level=level, max_rows=max_rows)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)
    _installed = handler
    return handler


def uninstall() -> None:
    """Detach and stop the handler. For tests, and for a clean shutdown."""
    global _installed
    if _installed is None:
        return
    logging.getLogger().removeHandler(_installed)
    _installed.close()
    _installed = None
