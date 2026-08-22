"""The log handler's job is to never be the reason a service stops.

It is attached to the root logger of processes that must keep running, so the
properties worth testing are all refusals: it does not block on a slow
database, it does not grow without bound, it does not recurse when its own
write fails, and it does not raise into the caller. Whether the row lands in
Postgres correctly is a matter for the live stack; what is here is everything
that must hold when Postgres is *not* behaving.
"""

import json
import logging
import queue
import threading
import time

import pytest
from common import dblog


class Sink:
    """What the writer thread would have sent to Postgres."""

    def __init__(self):
        self.rows: list[tuple] = []
        self.trims: list[tuple] = []

    # -- enough of psycopg to satisfy `_write` --------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def cursor(self):
        return self

    def executemany(self, sql, rows):
        self.rows.extend(rows)

    def execute(self, sql, params=None):
        self.trims.append(params)

    # -- what the tests read --------------------------------------------------

    def messages(self) -> list[str]:
        return [row[4] for row in self.rows]

    def wait_for(self, count: int, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(self.rows) < count:
            time.sleep(0.02)


@pytest.fixture(autouse=True)
def written(monkeypatch):
    """Keeps the suite off a real database.

    Without this the writer thread tries to connect for real, which is slow,
    noisy, and nothing to do with what these tests are about.
    """
    import common.db

    sink = Sink()
    monkeypatch.setattr(common.db, "connect", lambda **kwargs: sink)
    return sink


@pytest.fixture
def handler():
    made = []

    def build(**kwargs):
        h = dblog.DatabaseHandler("test-service", **kwargs)
        h.setFormatter(logging.Formatter("%(message)s"))
        made.append(h)
        return h

    yield build
    for h in made:
        h.close()


def _record(message="something went wrong", level=logging.WARNING, exc_info=None):
    return logging.LogRecord(
        name="test.logger", level=level, pathname=__file__, lineno=42,
        msg=message, args=(), exc_info=exc_info,
    )


def test_emitting_does_not_touch_the_database_on_the_caller_thread(handler):
    """The point of the queue: `emit` returns before anything is written."""
    h = handler()
    caller = threading.current_thread()
    h.emit(_record())
    assert h._queue.qsize() >= 1
    assert h._thread is not caller


def test_a_full_queue_drops_rather_than_blocking(handler, monkeypatch):
    """A service that stalls on its own logging is worse than one with a gap."""
    monkeypatch.setattr(dblog, "MAX_PENDING", 3)
    h = handler()
    # Stop the writer draining, so the queue genuinely fills.
    h._queue = queue.Queue(maxsize=3)

    started = time.monotonic()
    for _ in range(50):
        h.emit(_record())
    assert time.monotonic() - started < 1.0, "emit blocked"
    assert h._dropped >= 40


def test_the_dropped_count_becomes_a_row_of_its_own(handler, written):
    """A gap in the log has to say it is a gap, or it reads as a quiet period."""
    h = handler()
    h._dropped = 17
    h.emit(_record("a message that did get through"))

    written.wait_for(2)
    messages = written.messages()
    assert "a message that did get through" in messages
    assert any("17 log line(s) were dropped" in m for m in messages)


def test_a_row_reaches_the_database_with_its_service_and_level(handler, written):
    h = handler()
    h.emit(_record("resolution stopped responding", level=logging.ERROR))

    written.wait_for(1)
    service, level, level_no, logger_name, message, _detail = written.rows[0]
    assert (service, level, level_no) == ("test-service", "ERROR", logging.ERROR)
    assert logger_name == "test.logger"
    assert message == "resolution stopped responding"


def test_the_writer_thread_does_not_log_itself_into_a_loop(handler):
    """One failed insert logs a warning, which becomes a row, which fails to
    insert, which logs a warning. The thread check is what stops that."""
    h = handler()
    seen = []
    original = h._queue.put_nowait
    h._queue.put_nowait = lambda row: seen.append(row) or original(row)

    done = threading.Event()

    def pretend_to_be_the_writer():
        h._thread = threading.current_thread()
        h.emit(_record("could not write 5 log row(s)"))
        done.set()

    worker = threading.Thread(target=pretend_to_be_the_writer)
    worker.start()
    worker.join(timeout=2)
    assert done.is_set()
    assert seen == [], "the writer's own warning was queued"


def test_a_traceback_is_carried_in_the_detail(handler):
    h = handler()
    try:
        raise ValueError("column 'email' is not valid utf-8")
    except ValueError:
        import sys
        record = _record("failed", level=logging.ERROR, exc_info=sys.exc_info())
    detail = json.loads(h._detail(record))
    assert detail["line"] == 42
    assert "ValueError" in detail["traceback"]


def test_a_record_below_the_level_is_not_kept(handler, written):
    """INFO is a firehose and Postgres is not a log store."""
    h = handler()
    logger = logging.getLogger("test.level")
    logger.addHandler(h)
    logger.setLevel(logging.DEBUG)
    try:
        logger.info("ordinary progress")
        logger.warning("something unusual")
    finally:
        logger.removeHandler(h)

    written.wait_for(1)
    assert written.messages() == ["something unusual"]


def test_the_table_is_trimmed_so_a_warning_loop_costs_a_bounded_amount(
    handler, written
):
    """Postgres is not a log store. A service stuck warning in a loop should
    cost a fixed number of rows, not a page."""
    h = handler(max_rows=500)
    h.emit(_record())
    written.wait_for(1)

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not written.trims:
        time.sleep(0.02)
    assert written.trims == [(500,)]


def test_install_is_idempotent_and_survives_basicConfig(monkeypatch):
    """`common.logging.configure` calls basicConfig(force=True), which drops
    every root handler. Installing again must re-attach, not build a second."""
    dblog.uninstall()
    try:
        first = dblog.install("svc")
        logging.basicConfig(force=True)
        assert first not in logging.getLogger().handlers
        second = dblog.install("svc")
        assert second is first
        assert first in logging.getLogger().handlers
    finally:
        dblog.uninstall()
        logging.basicConfig(force=True)
