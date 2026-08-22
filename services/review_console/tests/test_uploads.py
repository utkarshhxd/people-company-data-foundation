"""Route-level tests for dropping files through the browser.

Nothing is ingested inside the request any more -- the bytes are written to
disk and a row goes into `ingest_queue`, which a single worker drains later
(see `ingest_queue.py`). So what is under test here is the receiving half: the
HTTP contract, what a client is allowed to name a file, and how much it is
allowed to send. `ingest_queue.enqueue` is stubbed, because whether Postgres
accepts the row is not this file's question.

Whether `ingestion.pipeline.ingest()` itself ingests correctly is that
package's own test suite's job -- the same division `test_stages.py` draws
around the five stage pipelines.
"""

import pytest
from fastapi.testclient import TestClient
from review_console import ingest_queue, uploads
from review_console.auth import ENV_ALLOW_UNAUTH
from review_console.main import app

client = TestClient(app)

CSV = b"full_name,email\nAda,ada@example.com\n"


@pytest.fixture(autouse=True)
def _serve_without_a_key(monkeypatch):
    monkeypatch.setenv(ENV_ALLOW_UNAUTH, "true")


@pytest.fixture(autouse=True)
def _isolate_upload_dir(monkeypatch, tmp_path):
    """Real files still get written -- just not into the container's /data,
    which will not exist on the test host."""
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)


@pytest.fixture
def queued(monkeypatch):
    """Capture what would have been written to `ingest_queue`."""
    rows = []

    def fake_enqueue(**kwargs):
        row = dict(kwargs, queue_id=f"q-{len(rows)}", status="queued")
        row["stored_path"] = str(row["stored_path"])
        rows.append(row)
        return row

    monkeypatch.setattr(ingest_queue, "enqueue", fake_enqueue)
    return rows


def _post(files, **form):
    body = {"entity_type": "person", "source_name": "test_vendor",
            "batch_size": "5000"}
    body.update(form)
    return client.post("/dashboard/ingest", data=body, files=files)


def _one(name="people.csv", content=CSV):
    return [("files", (name, content, "text/csv"))]


# --------------------------------------------------------------------------
# the HTTP contract
# --------------------------------------------------------------------------


def test_a_dropped_file_is_queued_and_nothing_is_ingested_in_the_request(queued):
    resp = _post(_one())
    assert resp.status_code == 200
    assert resp.json()["queued"] == 1
    assert queued[0]["entity_type"] == "person"
    assert queued[0]["source_name"] == "test_vendor"
    assert queued[0]["batch_size"] == 5000
    assert queued[0]["status"] == "queued"


def test_many_files_in_one_drop_become_many_queue_rows(queued):
    """The whole point: ten files is a queue, not ten concurrent loads."""
    files = [("files", (f"part{n}.csv", CSV, "text/csv")) for n in range(5)]
    resp = _post(files)
    assert resp.json()["queued"] == 5
    assert [row["file_name"] for row in queued] == [
        "part0.csv", "part1.csv", "part2.csv", "part3.csv", "part4.csv"
    ]
    # One set of arguments across the drop -- entity type and source name are
    # properties of the feed, not of the file.
    assert {row["source_name"] for row in queued} == {"test_vendor"}


def test_every_queued_file_gets_its_own_path_even_with_the_same_name(queued, tmp_path):
    files = [("files", ("export.csv", CSV, "text/csv")) for _ in range(3)]
    _post(files)
    paths = {row["stored_path"] for row in queued}
    assert len(paths) == 3, "two files collided on disk"
    assert len(list(tmp_path.iterdir())) == 3


def test_the_type_is_inferred_from_the_extension_when_not_given(queued):
    _post([("files", ("book.xlsx", b"PK\x03\x04", "application/vnd.ms-excel"))])
    assert queued[0]["source_type"] == "excel"
    _post(_one())
    assert queued[1]["source_type"] == "csv"


def test_a_sheet_name_is_only_applied_to_a_single_file_drop(queued):
    """One sheet name cannot mean anything sensible across several workbooks."""
    _post(_one("book.xlsx"), sheet="Apollo")
    assert queued[0]["sheet"] == "Apollo"

    files = [("files", (f"book{n}.xlsx", CSV, "text/csv")) for n in range(2)]
    _post(files, sheet="Apollo")
    assert [row["sheet"] for row in queued[1:]] == [None, None]


def test_the_person_who_dropped_it_is_recorded(queued):
    _post(_one(), queued_by="ada")
    assert queued[0]["queued_by"] == "ada"


def test_a_drop_with_no_files_is_refused():
    resp = client.post(
        "/dashboard/ingest",
        data={"entity_type": "person", "source_name": "v", "batch_size": "5000"},
    )
    assert resp.status_code == 422


def test_status_of_an_unknown_queue_item_is_a_404(monkeypatch):
    monkeypatch.setattr(ingest_queue, "get", lambda queue_id: None)
    assert client.get("/dashboard/queue/does-not-exist").status_code == 404


def test_cancelling_something_that_is_no_longer_waiting_is_a_409(monkeypatch):
    def refuse(queue_id, reviewed_by):
        raise ingest_queue.NotWaiting("already running")

    monkeypatch.setattr(ingest_queue, "cancel", refuse)
    resp = client.post("/dashboard/queue/abc/cancel", json={"reviewed_by": "ada"})
    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"]


def test_upload_is_not_served_without_a_key(monkeypatch):
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setenv("PCDF_API_KEYS", "a-real-key")
    resp = _post(_one())
    assert resp.status_code == 401


def test_the_queue_is_not_served_without_a_key(monkeypatch):
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setenv("PCDF_API_KEYS", "a-real-key")
    assert client.get("/dashboard/queue").status_code == 401


# --------------------------------------------------------------------------
# what a client is allowed to name, and how much it is allowed to send
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../etc/cron.d/pwn",
        "../watch/apollo/feed.json",
        "a/../../b.csv",
        "/abs/evil.csv",
        r"..\..\windows\system32\evil.csv",
    ],
)
def test_a_claimed_filename_cannot_escape_the_upload_directory(
    queued, tmp_path, hostile
):
    """The client picks the filename, so it is input, not a path.

    `../watch/<feed>/feed.json` is the one that matters: it stays inside the
    ./data bind mount and lands on a watcher feed's config, which decides the
    entity_type, source_name and reliability every future file in that feed is
    loaded under. Permission to upload must not become permission to rewrite
    how other people's files are read.
    """
    resp = _post(_one(hostile))
    assert resp.status_code == 200

    written = list(tmp_path.rglob("*"))
    assert written, "the upload was not saved at all"
    for path in written:
        assert path.parent == tmp_path, f"{path} escaped {tmp_path}"
        assert ".." not in path.name


def test_a_harmless_filename_stays_recognisable(queued, tmp_path):
    """Sanitising must not make it impossible to tell which upload was which."""
    _post(_one("Q3 export (final).csv"))
    names = [p.name for p in tmp_path.iterdir()]
    assert len(names) == 1
    assert names[0].endswith("Q3_export__final_.csv")


def test_an_oversized_upload_is_refused_rather_than_buffered(
    queued, monkeypatch, tmp_path
):
    """The cap is enforced while reading, not after.

    Reading the body in full before checking its size is the same as having no
    cap: the memory is already spent by the time anything can object.
    """
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1024)
    resp = _post(_one("big.csv", b"x" * 4096))
    assert resp.status_code == 413
    assert "limit" in resp.json()["detail"]
    assert list(tmp_path.iterdir()) == [], "the partial file was left behind"
    assert queued == [], "an over-size file was queued anyway"


def test_files_queued_before_an_over_size_one_are_kept(queued, monkeypatch, tmp_path):
    """Throwing away files that were fine because a later one was too big would
    mean re-uploading work that never failed."""
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1024)
    resp = _post([
        ("files", ("small.csv", CSV, "text/csv")),
        ("files", ("big.csv", b"x" * 4096, "text/csv")),
    ])
    assert resp.status_code == 413
    assert "1 earlier file(s) were queued" in resp.json()["detail"]
    assert [row["file_name"] for row in queued] == ["small.csv"]
