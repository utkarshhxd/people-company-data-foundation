"""What becomes of one queued file, and when the worker declines to take one.

`ingest()` and the database are both stubbed. What is under test is the part
that decides: which outcome a given failure is recorded as, and -- the one that
matters operationally -- that a stage being paused holds a file rather than
failing it. A held file needs nothing; a failed one invites somebody to
re-upload a file that was never wrong.
"""

import json
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from common import control
from ingestion.pipeline import ReingestBlocked
from ingestion.readers import MultipleSheets
from review_console import ingest_queue


@dataclass
class FakeIngestResult:
    batch_id: str
    source_id: str
    rows_read: int
    rows_ingested: int
    rows_skipped: int


ITEM = {
    "queue_id": "0199-item",
    "file_name": "people.csv",
    "stored_path": "/data/inbox/uploads/abc__people.csv",
    "entity_type": "person",
    "source_name": "apollo",
    "source_type": "csv",
    "reliability": 0.7,
    "record_id_column": None,
    "allow_reingest": False,
    "batch_size": 5000,
    "describes": None,
    "sheet": None,
}


@pytest.fixture
def finished(monkeypatch):
    """Capture the single terminal write `_process` makes."""
    calls = []
    monkeypatch.setattr(
        ingest_queue, "_finish",
        lambda queue_id, status, **fields: calls.append((queue_id, status, fields)),
    )
    return calls


@pytest.fixture(autouse=True)
def _no_heartbeat_thread(monkeypatch):
    """The ticker opens its own connection; it has nothing to say here."""

    class Quiet:
        def __init__(self, queue_id):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(ingest_queue, "_Ticker", Quiet)


def _ingest_that(monkeypatch, behaviour):
    monkeypatch.setattr(ingest_queue, "_INGEST", behaviour)


# --------------------------------------------------------------------------
# outcomes
# --------------------------------------------------------------------------


def test_a_load_that_works_records_the_batch_and_its_counts(monkeypatch, finished):
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return FakeIngestResult("batch-9", "source-1", 120, 118, 2)

    _ingest_that(monkeypatch, fake)
    ingest_queue._process(dict(ITEM))

    queue_id, status, fields = finished[0]
    assert (queue_id, status) == ("0199-item", "completed")
    assert fields["batch_id"] == "batch-9"
    assert fields["rows_read"] == 120
    assert fields["rows_ingested"] == 118
    assert fields["rows_skipped"] == 2
    # Every argument the operator chose is handed to ingest() unchanged.
    assert seen["source_name"] == "apollo"
    assert seen["reliability"] == 0.7
    assert seen["batch_size"] == 5000


def test_a_blocked_reingest_fails_with_its_exact_message(monkeypatch, finished):
    def fake(**kwargs):
        raise ReingestBlocked("people.csv was already ingested for source 'apollo'")

    _ingest_that(monkeypatch, fake)
    ingest_queue._process(dict(ITEM))

    _, status, fields = finished[0]
    assert status == "failed"
    assert "already ingested" in fields["error"]


def test_a_multi_sheet_workbook_hands_back_its_sheet_names(monkeypatch, finished):
    """So the console can offer them as picks rather than asking somebody to
    retype a name they just read out of an error string."""
    def fake(**kwargs):
        raise MultipleSheets("has 2 sheets with data (Apollo, Lead411)")

    _ingest_that(monkeypatch, fake)
    monkeypatch.setattr(ingest_queue, "sheet_names", lambda path: ["Apollo", "Lead411"])
    ingest_queue._process(dict(ITEM))

    _, status, fields = finished[0]
    assert status == "failed"
    assert json.loads(fields["detail"]) == {"available_sheets": ["Apollo", "Lead411"]}


def test_a_workbook_whose_sheets_cannot_be_listed_still_fails_cleanly(
    monkeypatch, finished
):
    def fake(**kwargs):
        raise MultipleSheets("has several sheets")

    def explode(path):
        raise OSError("file vanished")

    _ingest_that(monkeypatch, fake)
    monkeypatch.setattr(ingest_queue, "sheet_names", explode)
    ingest_queue._process(dict(ITEM))

    _, status, fields = finished[0]
    assert status == "failed"
    assert json.loads(fields["detail"]) == {"available_sheets": []}


def test_ingestion_being_stopped_holds_the_file_rather_than_failing_it(
    monkeypatch, finished
):
    """A held file needs nothing done to it. A failed one invites a re-upload
    of a file that was never wrong."""
    state = control.ControlState(
        "ingestion", control.PAUSED, "investigating a vendor", "ada", {},
        __import__("datetime").datetime(2026, 8, 22),
    )

    def fake(**kwargs):
        raise control.StagePaused(state)

    _ingest_that(monkeypatch, fake)
    ingest_queue._process(dict(ITEM))

    _, status, fields = finished[0]
    assert status == "held"
    assert "investigating a vendor" in fields["error"]
    # Not finished, because it has not been tried yet -- it is still waiting.
    assert fields["started_at"] is None


def test_any_other_failure_is_recorded_rather_than_raised(monkeypatch, finished):
    """One bad file must cost one file, not the worker."""
    def fake(**kwargs):
        raise ValueError("column 'email' is not valid utf-8")

    _ingest_that(monkeypatch, fake)
    ingest_queue._process(dict(ITEM))  # must not raise

    _, status, fields = finished[0]
    assert status == "failed"
    assert "utf-8" in fields["error"]


# --------------------------------------------------------------------------
# when the worker declines to take anything at all
# --------------------------------------------------------------------------


@pytest.fixture
def fake_db(monkeypatch):
    """A connection that does nothing, so `_sweep` can be reasoned about."""
    class Conn:
        def commit(self):
            pass

    @contextmanager
    def fake_connect(*args, **kwargs):
        yield Conn()

    monkeypatch.setattr(ingest_queue, "connect", fake_connect)
    monkeypatch.setattr(ingest_queue, "_release_abandoned", lambda conn: 0)


def _paused(monkeypatch, paused: bool):
    state = control.ControlState(
        "ingestion", control.PAUSED if paused else control.RUNNING,
        "stopped" if paused else None, "ada", {}, None,
    )
    monkeypatch.setattr(control, "get", lambda conn, stage: state)


def test_nothing_is_claimed_while_ingestion_is_stopped(monkeypatch, fake_db):
    claimed = []
    monkeypatch.setattr(ingest_queue, "_claim", lambda conn: claimed.append(1))
    monkeypatch.setattr(ingest_queue, "_set_waiting_state", lambda conn, to: 0)
    _paused(monkeypatch, True)

    assert ingest_queue.Worker()._sweep() is None
    assert claimed == []


def test_waiting_files_are_held_on_the_way_down_and_released_on_the_way_up(
    monkeypatch, fake_db
):
    moves = []
    monkeypatch.setattr(ingest_queue, "_claim", lambda conn: None)
    monkeypatch.setattr(
        ingest_queue, "_set_waiting_state",
        lambda conn, to: (moves.append(to), 2)[1],
    )
    worker = ingest_queue.Worker()

    _paused(monkeypatch, True)
    worker._sweep()
    _paused(monkeypatch, False)
    worker._sweep()
    assert moves == ["held", "queued"]


def test_a_file_dropped_during_a_pause_is_held_too(monkeypatch, fake_db):
    """The case that matters, and the one a transition-only check missed.

    A file uploaded while ingestion is already stopped arrives after the
    transition has been handled. Reconciling only on the change left it sitting
    as `queued` with nothing coming for it and nothing saying why.
    """
    moves = []
    monkeypatch.setattr(ingest_queue, "_claim", lambda conn: None)
    monkeypatch.setattr(
        ingest_queue, "_set_waiting_state",
        lambda conn, to: (moves.append(to), 1)[1],
    )
    _paused(monkeypatch, True)

    worker = ingest_queue.Worker()
    worker._sweep()          # the transition itself
    moves.clear()
    worker._sweep()          # a later pass, with the pause unchanged
    assert moves == ["held"], "a file arriving mid-pause would never be held"
