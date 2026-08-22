"""Ingestion refuses to start a load when intake has been stopped.

Only the guard is tested here, and deliberately without a database: what
matters is *where* the refusal happens, not what the rest of `ingest()` does
afterwards. It has to be before the batch row exists and before the source is
touched, or a stopped pipeline leaves a `running` batch nobody asked for --
and ingestion's own duplicate check would then refuse to load that file again
once somebody starts it back up.

Whether a file is read and stored correctly is `test_readers.py`'s job, and
the live stack's.
"""

import pytest
from common import control
from ingestion import pipeline, repository


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def commit(self):  # pragma: no cover - never reached in these tests
        raise AssertionError("a stopped load committed something")


@pytest.fixture
def stopped(monkeypatch, tmp_path):
    """Intake paused, no database, and a file that really exists on disk."""
    monkeypatch.setattr(pipeline, "connect", lambda *a, **k: FakeConn())

    def get(conn, stage):
        paused = stage == "ingestion"
        return control.ControlState(
            stage, control.PAUSED if paused else control.RUNNING,
            "resolution-consumer is not responding" if paused else None,
            "supervisor", {}, None,
        )

    monkeypatch.setattr(control, "get", get)

    path = tmp_path / "people.csv"
    path.write_text("full_name,email\nAda,ada@example.com\n", encoding="utf-8")
    return path


def _ingest(path):
    return pipeline.ingest(
        path=path, entity_type="person", source_name="vendor_x",
        source_type="csv", reliability=0.8,
    )


def test_a_stopped_intake_refuses_the_load(stopped):
    with pytest.raises(control.StagePaused) as raised:
        _ingest(stopped)
    # The message carries why and who, because whoever hits this is rarely the
    # one who stopped it -- and here it may not have been a person at all.
    assert "resolution-consumer is not responding" in str(raised.value)
    assert "supervisor" in str(raised.value)


def test_nothing_is_written_before_the_refusal(stopped, monkeypatch):
    """Not the batch, and not the source either.

    A `running` batch left behind by a load that never started would block the
    file from being loaded again once intake is running, which turns a pause
    into a thing somebody has to clean up.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("a stopped load touched the database")

    monkeypatch.setattr(repository, "get_or_create_source", refuse)
    monkeypatch.setattr(repository, "create_batch", refuse)

    with pytest.raises(control.StagePaused):
        _ingest(stopped)


def test_the_file_is_left_exactly_where_it_was(stopped):
    before = stopped.read_bytes()
    with pytest.raises(control.StagePaused):
        _ingest(stopped)
    assert stopped.is_file()
    assert stopped.read_bytes() == before
