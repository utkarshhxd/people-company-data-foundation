"""What the watcher must get right, none of which is throughput.

`run_file` is stubbed throughout. Whether the pipeline loads a file correctly is
covered by the rest of this suite; what is covered here is everything around it:
refusing a file that is still arriving, surviving one that fails, and never
loading anything under the wrong feed's settings.
"""

import json
from pathlib import Path

import pytest
from record_pipeline import watch
from record_pipeline.watch import (
    Feed,
    FeedInvalid,
    Handled,
    Watcher,
    candidate_files,
    discover_feeds,
    load_feed,
    process_file,
)


class FakeResult:
    batch_id = "01a0146b-0000-7000-8000-000000000000"
    rows_read = 3
    processed = 3
    failed = 0
    quarantined = 0
    invalid = 0
    linked = 1
    new_entities = 2
    review = 0
    mapping_reused = False


@pytest.fixture
def root(tmp_path):
    return tmp_path / "watch"


def make_feed(root: Path, name: str, **overrides) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    config = {"entity_type": "person", "source_name": name, "reliability": 0.8}
    config.update(overrides)
    (directory / "feed.json").write_text(json.dumps(config), encoding="utf-8")
    return directory


def drop(directory: Path, name: str, content: str = "a,b\n1,2\n") -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def loaded(monkeypatch):
    """Record every run_file call instead of running the pipeline."""
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return FakeResult()

    monkeypatch.setattr(watch, "run_file", fake)
    return calls


# --------------------------------------------------------------------------
# feed configuration
# --------------------------------------------------------------------------


def test_a_feed_carries_the_arguments_so_a_file_does_not_have_to(root):
    directory = make_feed(root, "apollo", describes="organisation", reliability=0.9)
    feed = load_feed(directory)
    assert (feed.entity_type, feed.source_name) == ("person", "apollo")
    assert (feed.reliability, feed.describes) == (0.9, "organisation")


def test_a_mistyped_entity_type_is_refused_before_it_loads_anything(root):
    directory = make_feed(root, "bad", entity_type="persons")
    # The cost of getting this wrong is a company's rows resolved as people,
    # recoverable only by purging the source.
    with pytest.raises(FeedInvalid, match="entity_type"):
        load_feed(directory)


def test_an_out_of_range_reliability_is_refused(root):
    directory = make_feed(root, "bad", reliability=7)
    with pytest.raises(FeedInvalid, match="reliability"):
        load_feed(directory)


def test_an_unknown_describes_is_refused(root):
    directory = make_feed(root, "bad", describes="premises")
    with pytest.raises(FeedInvalid, match="describes"):
        load_feed(directory)


def test_malformed_json_names_the_file_it_could_not_read(root):
    directory = root / "broken"
    directory.mkdir(parents=True)
    (directory / "feed.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(FeedInvalid, match="not valid JSON"):
        load_feed(directory)


def test_one_broken_feed_does_not_hide_the_others(root):
    make_feed(root, "good")
    make_feed(root, "broken", entity_type="nonsense")
    (root / "no_config").mkdir()

    feeds, problems = discover_feeds(root)
    assert [f.name for f in feeds] == ["good"]
    assert len(problems) == 2


# --------------------------------------------------------------------------
# which files count
# --------------------------------------------------------------------------


def test_the_config_and_the_receipts_are_not_data(root):
    directory = make_feed(root, "apollo")
    drop(directory, "export.csv")
    drop(directory, "export.csv.log", "a receipt")
    drop(directory, "half.part", "still downloading")
    drop(directory, ".hidden.csv")

    assert [p.name for p in candidate_files(load_feed(directory))] == ["export.csv"]


def test_a_backlog_is_worked_oldest_first(root):
    directory = make_feed(root, "apollo")
    first = drop(directory, "monday.csv")
    second = drop(directory, "tuesday.csv")
    import os
    os.utime(first, (1_000, 1_000))
    os.utime(second, (2_000, 2_000))

    # Daily exports have to be applied in arrival order for "most recent wins"
    # to mean what it says.
    assert [p.name for p in candidate_files(load_feed(directory))] == [
        "monday.csv", "tuesday.csv",
    ]


# --------------------------------------------------------------------------
# still-arriving files
# --------------------------------------------------------------------------


def test_a_file_is_not_touched_the_first_time_it_is_seen(root, loaded):
    directory = make_feed(root, "apollo")
    drop(directory, "export.csv")

    watcher = Watcher(root, poll_seconds=0)
    assert watcher.sweep() == []
    # A 200 MB export is a valid, truncated CSV for as long as the copy takes.
    assert loaded == []


def test_a_file_is_processed_once_it_has_stopped_changing(root, loaded):
    directory = make_feed(root, "apollo")
    drop(directory, "export.csv")

    watcher = Watcher(root, poll_seconds=0)
    watcher.sweep()
    handled = watcher.sweep()

    assert [h.ok for h in handled] == [True]
    assert len(loaded) == 1


def test_a_file_that_grew_between_sweeps_is_still_left_alone(root, loaded):
    directory = make_feed(root, "apollo")
    path = drop(directory, "export.csv")

    watcher = Watcher(root, poll_seconds=0)
    watcher.sweep()
    path.write_text("a,b\n1,2\n3,4\n5,6\n", encoding="utf-8")
    assert watcher.sweep() == []
    assert loaded == []


# --------------------------------------------------------------------------
# loading under the feed's settings
# --------------------------------------------------------------------------


def test_every_file_is_loaded_under_its_own_feeds_settings(root, loaded):
    people = make_feed(root, "apollo", entity_type="person", reliability=0.9)
    firms = make_feed(root, "dnb", entity_type="company", reliability=0.4,
                      describes="organisation")
    drop(people, "contacts.csv")
    drop(firms, "companies.csv")

    watcher = Watcher(root, poll_seconds=0)
    watcher.sweep()
    watcher.sweep()

    by_source = {call["source_name"]: call for call in loaded}
    assert by_source["apollo"]["entity_type"] == "person"
    assert by_source["apollo"]["reliability"] == 0.9
    assert by_source["dnb"]["entity_type"] == "company"
    assert by_source["dnb"]["describes"] == "organisation"


def test_an_excel_file_is_read_as_excel_without_being_told(root, loaded):
    directory = make_feed(root, "apollo")
    drop(directory, "export.xlsx")

    watcher = Watcher(root, poll_seconds=0)
    watcher.sweep()
    watcher.sweep()
    assert loaded[0]["source_type"] == "excel"


# --------------------------------------------------------------------------
# what becomes of a file
# --------------------------------------------------------------------------


def test_a_processed_file_is_filed_with_a_receipt(root, loaded):
    directory = make_feed(root, "apollo")
    path = drop(directory, "export.csv")
    feed = load_feed(directory)

    process_file(feed, path)

    assert not path.exists()
    moved = list(feed.done_dir.glob("*export.csv"))
    assert len(moved) == 1
    receipt = moved[0].with_suffix(moved[0].suffix + ".log").read_text(encoding="utf-8")
    assert "processed 3" in receipt


def test_a_file_that_throws_is_moved_aside_with_the_reason(root, monkeypatch):
    directory = make_feed(root, "apollo")
    path = drop(directory, "export.csv")
    feed = load_feed(directory)

    def boom(**_kwargs):
        raise RuntimeError("postgres went away")

    monkeypatch.setattr(watch, "run_file", boom)
    handled = process_file(feed, path)

    assert handled.ok is False
    moved = list(feed.failed_dir.glob("*export.csv"))
    assert len(moved) == 1
    reason = moved[0].with_suffix(moved[0].suffix + ".log").read_text(encoding="utf-8")
    assert "postgres went away" in reason


def test_one_failing_file_does_not_stop_the_next_one(root, monkeypatch):
    directory = make_feed(root, "apollo")
    drop(directory, "aaa_broken.csv")
    drop(directory, "zzz_fine.csv")
    processed = []

    def sometimes(**kwargs):
        if "broken" in kwargs["path"].name:
            raise RuntimeError("bad file")
        processed.append(kwargs["path"].name)
        return FakeResult()

    monkeypatch.setattr(watch, "run_file", sometimes)
    watcher = Watcher(root, poll_seconds=0)
    watcher.sweep()
    handled = watcher.sweep()

    # The watcher is a long-running process whose whole job is to still be
    # running tomorrow.
    assert sorted(h.ok for h in handled) == [False, True]
    assert processed == ["zzz_fine.csv"]


def test_the_same_bytes_arriving_again_is_done_not_failed(root, monkeypatch):
    from ingestion.pipeline import ReingestBlocked

    directory = make_feed(root, "apollo")
    path = drop(directory, "export.csv")
    feed = load_feed(directory)

    def already(**_kwargs):
        raise ReingestBlocked("file already ingested for source apollo")

    monkeypatch.setattr(watch, "run_file", already)
    handled = process_file(feed, path)

    # Nothing went wrong: the data is already loaded. Filing it as failed would
    # send somebody to investigate a system working correctly.
    assert handled.ok is True
    assert list(feed.done_dir.glob("*export.csv"))
    assert not feed.failed_dir.exists()


def test_a_second_file_of_the_same_name_never_overwrites_the_first(root, loaded):
    directory = make_feed(root, "apollo")
    feed = load_feed(directory)

    process_file(feed, drop(directory, "export.csv", "a,b\n1,2\n"))
    process_file(feed, drop(directory, "export.csv", "a,b\n3,4\n"))

    # Two deliveries of the same name are two deliveries. Replacing the first
    # would destroy the evidence of what was actually loaded.
    assert len(list(feed.done_dir.glob("*export.csv"))) == 2


# --------------------------------------------------------------------------
# stopping
# --------------------------------------------------------------------------


def test_a_stop_request_is_honoured_between_files(root, monkeypatch):
    directory = make_feed(root, "apollo")
    drop(directory, "one.csv")
    drop(directory, "two.csv")
    watcher = Watcher(root, poll_seconds=0)

    def stop_after_first(**kwargs):
        watcher.request_stop()
        return FakeResult()

    monkeypatch.setattr(watch, "run_file", stop_after_first)
    watcher.sweep()
    handled = watcher.sweep()

    # The file in hand finishes; the next one is left for the next start.
    assert len(handled) == 1


def test_a_missing_watch_root_is_a_complaint_not_a_crash(tmp_path):
    feeds, problems = discover_feeds(tmp_path / "nothing-here")
    assert feeds == []
    assert "does not exist" in problems[0]


def test_the_feed_dataclass_names_its_own_directories(tmp_path):
    feed = Feed("apollo", tmp_path, "person", "apollo", 0.5)
    assert feed.done_dir == tmp_path / "_done"
    assert feed.failed_dir == tmp_path / "_failed"


# --------------------------------------------------------------------------
# liveness
# --------------------------------------------------------------------------


def test_a_completed_sweep_is_recorded_as_a_heartbeat(tmp_path):
    """`restart: unless-stopped` only acts on a process that exited.

    A watcher wedged inside a sweep keeps its container "up" while nothing is
    being loaded, and from outside that is indistinguishable from an idle one.
    The heartbeat is what makes staleness observable.
    """
    beat = tmp_path / "beat"
    watcher = Watcher(tmp_path / "watch", poll_seconds=1, heartbeat=beat)
    assert not beat.exists()

    watcher.sweep()
    watcher._beat()
    assert beat.exists()
    first = beat.read_text(encoding="utf-8")
    assert first  # an ISO timestamp, not an empty file


def test_each_finished_file_beats_so_a_backlog_is_not_mistaken_for_a_hang(
    tmp_path, monkeypatch
):
    beat = tmp_path / "beat"
    root = tmp_path / "watch"
    feed_dir = root / "vendor"
    feed_dir.mkdir(parents=True)
    (feed_dir / "feed.json").write_text(
        json.dumps({"entity_type": "person", "source_name": "vendor"}), encoding="utf-8"
    )
    for name in ("a.csv", "b.csv"):
        (feed_dir / name).write_text("full_name\nAda\n", encoding="utf-8")

    beats = []
    monkeypatch.setattr(
        watch, "process_file", lambda feed, path: Handled(path, True, "loaded")
    )

    watcher = Watcher(root, poll_seconds=1, heartbeat=beat)
    original_beat = watcher._beat

    def counting_beat():
        beats.append(1)
        original_beat()

    watcher._beat = counting_beat
    watcher.sweep()  # first pass only fingerprints; nothing has settled yet
    handled = watcher.sweep()

    assert len(handled) == 2
    assert len(beats) == 2, "each finished file should beat"


def test_an_unwritable_heartbeat_does_not_stop_the_watcher(tmp_path, caplog):
    """A monitoring gap must not be traded for an outage."""
    watcher = Watcher(tmp_path / "watch", poll_seconds=1, heartbeat=tmp_path)
    watcher._beat()  # tmp_path is a directory: writing to it raises OSError
    assert any("heartbeat" in r.message for r in caplog.records)


# --------------------------------------------------------------------------
# the two paths must not both process one batch
# --------------------------------------------------------------------------


def test_the_record_at_a_time_path_never_starts_the_consumer_chain():
    """`pcdf.batch.ingested` is what wakes the five stage consumers.

    A file loaded here has already been carried to its golden values inside one
    transaction per record, so announcing it on that topic would hand the same
    batch to the batch chain as well. Two builders on one entity is exactly the
    collision ADR 0012 recorded (a batch CLI run while the consumer chain was
    working the same batch), and the reason it cannot happen here is that this
    path is structurally silent on that topic -- not that anyone remembers.

    Read off the syntax tree rather than the source text, so a comment naming
    the topic (there is one, explaining this) is not mistaken for a publish.
    """
    import ast

    from record_pipeline import runner

    tree = ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
    topics = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr.startswith("TOPIC_")
    }
    assert topics == {"TOPIC_RECORD_PROCESSED"}, (
        f"the record-at-a-time path publishes {topics}; only the per-record "
        "event belongs here"
    )


# --------------------------------------------------------------------------
# stopping
# --------------------------------------------------------------------------


def _control_says(monkeypatch, paused: dict[str, str]):
    """Stand in for pipeline_control without a database."""
    from common import control

    def get(conn, stage):
        reason = paused.get(stage)
        return control.ControlState(
            stage, control.PAUSED if reason else control.RUNNING,
            reason, "ada", {}, None,
        )

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(watch, "connect", lambda *a, **k: Conn())
    monkeypatch.setattr(control, "get", get)


def test_the_watcher_stops_when_ingestion_is_stopped(root, loaded, monkeypatch):
    """The supervisor stops `ingestion` when a service that would process the
    work has gone away. The watcher is the main automated way in, so it has to
    be one of the things that stops -- otherwise files keep being loaded into a
    pipeline with nobody at the other end, which is the pile this exists to
    prevent."""
    make_feed(root, "vendor_x")
    (root / "vendor_x" / "people.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    _control_says(monkeypatch, {"ingestion": "resolution-consumer is not responding"})

    watcher = Watcher(root, poll_seconds=0)
    watcher.sweep()
    assert watcher.sweep() == []
    assert loaded == []
    # Left exactly where it was: a pause is not a rejection.
    assert (root / "vendor_x" / "people.csv").is_file()


def test_the_watcher_still_stops_when_validation_stops_itself(root, loaded, monkeypatch):
    make_feed(root, "vendor_x")
    (root / "vendor_x" / "people.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    _control_says(monkeypatch, {"validation": "98% of records came back invalid"})

    watcher = Watcher(root, poll_seconds=0)
    watcher.sweep()
    assert watcher.sweep() == []
    assert loaded == []


def test_the_reason_names_which_stage_stopped_it(root, monkeypatch):
    """Two stages stop a load for opposite reasons -- a missing service, and
    data that does not look like data. Saying only "paused" would send the
    investigation to the wrong place."""
    _control_says(monkeypatch, {"ingestion": "the broker is unreachable"})
    assert "ingestion is paused" in Watcher(root, poll_seconds=0)._paused()

    _control_says(monkeypatch, {"validation": "98% invalid"})
    assert "validation is paused" in Watcher(root, poll_seconds=0)._paused()

    _control_says(monkeypatch, {})
    assert Watcher(root, poll_seconds=0)._paused() is None


def test_an_unreachable_database_is_not_treated_as_a_pause(root, monkeypatch):
    """Stopping for that reason would hide the real one, and the load fails and
    is recorded on its own anyway."""
    def explode(*args, **kwargs):
        raise RuntimeError("could not connect to postgres")

    monkeypatch.setattr(watch, "connect", explode)
    assert Watcher(root, poll_seconds=0)._paused() is None
