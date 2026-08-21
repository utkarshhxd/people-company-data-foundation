"""Route-level tests for dropping a file through the browser.

`uploads._INGEST` is stubbed with a fake, instant function so these never
touch a real filesystem-backed ingest or database -- what's under test is the
HTTP contract: a multipart POST starts a background run and returns an upload
id immediately, the registry reflects completion (with the resulting
batch_id) once the fake ingest returns, a `ReingestBlocked`-style failure
carries its message, and the endpoint is key-gated like the rest of
`/dashboard/*`.

Whether `ingestion.pipeline.ingest()` itself ingests correctly is that
package's own test suite's job -- same division `test_stages.py` draws
around the five stage pipelines.
"""

import time
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from review_console import uploads
from review_console.auth import ENV_ALLOW_UNAUTH
from review_console.main import app

client = TestClient(app)


@dataclass
class FakeIngestResult:
    batch_id: str
    source_id: str
    rows_read: int
    rows_ingested: int
    rows_skipped: int


@pytest.fixture(autouse=True)
def _serve_without_a_key(monkeypatch):
    monkeypatch.setenv(ENV_ALLOW_UNAUTH, "true")


@pytest.fixture(autouse=True)
def _isolate_upload_dir(monkeypatch, tmp_path):
    """Real files still get written (start() does that itself) -- just not
    into the container's /data, which won't exist on the test host."""
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)


def _wait_until_not_running(upload_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = uploads.status(upload_id)
        if state is not None and state["status"] != "running":
            return state
        time.sleep(0.01)
    raise TimeoutError(f"upload {upload_id} never left 'running'")


def _post_file(monkeypatch, ingest_fn, **form):
    monkeypatch.setattr(uploads, "_INGEST", ingest_fn)
    body = {
        "entity_type": "person",
        "source_name": "test_vendor",
        "batch_size": "5000",
    }
    body.update(form)
    return client.post(
        "/dashboard/ingest",
        data=body,
        files={"file": ("people.csv", b"full_name,email\nAda,ada@example.com\n", "text/csv")},
    )


def test_uploading_starts_ingest_and_returns_immediately(monkeypatch):
    calls = []

    def fake_ingest(**kwargs):
        calls.append(kwargs)
        time.sleep(0.05)
        return FakeIngestResult("batch-1", "source-1", 1, 1, 0)

    resp = _post_file(monkeypatch, fake_ingest)
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert calls[0]["entity_type"] == "person"
    assert calls[0]["source_name"] == "test_vendor"
    assert calls[0]["batch_size"] == 5000


def test_the_registry_reflects_completion_with_the_new_batch_id(monkeypatch):
    def fake_ingest(**kwargs):
        return FakeIngestResult("batch-42", "source-9", 5, 5, 0)

    resp = _post_file(monkeypatch, fake_ingest)
    upload_id = resp.json()["upload_id"]
    state = _wait_until_not_running(upload_id)
    assert state["status"] == "completed"
    assert state["batch_id"] == "batch-42"
    assert state["detail"] == {"rows_read": 5, "rows_ingested": 5, "rows_skipped": 0}


def test_a_blocked_reingest_carries_its_exact_message(monkeypatch):
    from ingestion.pipeline import ReingestBlocked

    def fake_ingest(**kwargs):
        raise ReingestBlocked("people.csv was already ingested for source 'test_vendor'")

    resp = _post_file(monkeypatch, fake_ingest)
    upload_id = resp.json()["upload_id"]
    state = _wait_until_not_running(upload_id)
    assert state["status"] == "failed"
    assert "already ingested" in state["error"]


def test_a_multi_sheet_workbook_offers_its_sheet_names_back(monkeypatch):
    from ingestion.readers import MultipleSheets

    def fake_ingest(**kwargs):
        raise MultipleSheets("has 2 sheets with data (Apollo, Lead411)")

    monkeypatch.setattr(uploads, "sheet_names", lambda path: ["Apollo", "Lead411"])
    resp = _post_file(monkeypatch, fake_ingest)
    upload_id = resp.json()["upload_id"]
    state = _wait_until_not_running(upload_id)
    assert state["status"] == "failed"
    assert state["detail"] == {"available_sheets": ["Apollo", "Lead411"]}


def test_the_chosen_sheet_is_forwarded_to_ingest(monkeypatch):
    calls = []

    def fake_ingest(**kwargs):
        calls.append(kwargs.get("sheet"))
        return FakeIngestResult("batch-7", "source-1", 1, 1, 0)

    resp = _post_file(monkeypatch, fake_ingest, sheet="Apollo")
    _wait_until_not_running(resp.json()["upload_id"])
    assert calls == ["Apollo"]


def test_status_of_an_unknown_upload_is_a_404():
    resp = client.get("/dashboard/ingest/does-not-exist")
    assert resp.status_code == 404


def test_upload_is_not_served_without_a_key(monkeypatch):
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setenv("PCDF_API_KEYS", "a-real-key")
    resp = client.post(
        "/dashboard/ingest",
        data={"entity_type": "person", "source_name": "x"},
        files={"file": ("x.csv", b"a\n1\n", "text/csv")},
    )
    assert resp.status_code == 401


# --------------------------------------------------------------------------
# what a client is allowed to name, and how much it is allowed to send
# --------------------------------------------------------------------------


def _post_named(monkeypatch, file_name: str, content: bytes = b"a,b\n1,2\n"):
    monkeypatch.setattr(
        uploads, "_INGEST", lambda **k: FakeIngestResult("b", "s", 1, 1, 0)
    )
    return client.post(
        "/dashboard/ingest",
        data={"entity_type": "person", "source_name": "v", "batch_size": "5000"},
        files={"file": (file_name, content, "text/csv")},
    )


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
    monkeypatch, tmp_path, hostile
):
    """The client picks the filename, so it is input, not a path.

    `../watch/<feed>/feed.json` is the one that matters: it stays inside the
    ./data bind mount and lands on a watcher feed's config, which decides the
    entity_type, source_name and reliability every future file in that feed is
    loaded under. Permission to upload must not become permission to rewrite
    how other people's files are read.
    """
    resp = _post_named(monkeypatch, hostile)
    assert resp.status_code == 200

    written = list(tmp_path.rglob("*"))
    assert written, "the upload was not saved at all"
    for path in written:
        assert path.parent == tmp_path, f"{path} escaped {tmp_path}"
        assert ".." not in path.name


def test_a_harmless_filename_stays_recognisable(monkeypatch, tmp_path):
    """Sanitising must not make it impossible to tell which upload was which."""
    _post_named(monkeypatch, "Q3 export (final).csv")
    names = [p.name for p in tmp_path.iterdir()]
    assert len(names) == 1
    assert names[0].endswith("Q3_export__final_.csv")


def test_an_oversized_upload_is_refused_rather_than_buffered(monkeypatch, tmp_path):
    """The cap is enforced while reading, not after.

    Reading the body in full before checking its size is the same as having no
    cap: the memory is already spent by the time anything can object.
    """
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1024)
    resp = _post_named(monkeypatch, "big.csv", b"x" * 4096)
    assert resp.status_code == 413
    assert "limit" in resp.json()["detail"]
    assert list(tmp_path.iterdir()) == [], "the partial file was left behind"


def test_finished_uploads_do_not_accumulate_forever(monkeypatch):
    """The console is a long-running process; its registries must be bounded."""
    monkeypatch.setattr(uploads, "MAX_REMEMBERED_UPLOADS", 3)
    ids = []
    for _ in range(6):
        resp = _post_named(monkeypatch, "people.csv")
        upload_id = resp.json()["upload_id"]
        _wait_until_not_running(upload_id)
        ids.append(upload_id)

    remembered = [i for i in ids if uploads.status(i) is not None]
    assert len(remembered) <= 3
    # Oldest-first eviction: the most recent are the ones a browser is still
    # polling for, so they are the ones that must survive.
    assert ids[-1] in remembered
