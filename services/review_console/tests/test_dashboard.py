"""Route-level tests for the pipeline dashboard.

`pipeline.py`'s queries are stubbed, same as `test_review.py` stubs `review.py`.
What is under test is the HTTP contract: shapes, that a missing batch is a 404,
and that the dashboard is gated like everything else that shows record-level
provenance.

Whether the funnel SQL itself counts correctly is `pipeline.py`'s own concern,
verified against real data, not this file's -- this only proves the routes
hand back what the queries return.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from review_console import pipeline
from review_console.auth import ENV_ALLOW_UNAUTH
from review_console.main import app

client = TestClient(app)

NOW = datetime(2026, 8, 18, tzinfo=UTC)
BATCH = "019ff9ac-dc79-749b-9b84-6e5e19fc03a4"

FUNNEL = {
    "ingested": 233,
    "failed": 0,
    "normalized": 233,
    "validated": {"valid": 144, "warning": 89, "invalid": 0},
    "quarantine": {"open": 0, "released": 0, "rejected": 0, "resolved": 0},
    "resolved": {"auto_linked": 0, "new_entity": 233, "manual": 0},
    "golden_built": 233,
    "needs_review": {"mapping": 0, "candidates": 0},
}


@pytest.fixture(autouse=True)
def _serve_without_a_key(monkeypatch):
    monkeypatch.setenv(ENV_ALLOW_UNAUTH, "true")


@pytest.fixture
def stub_pipeline(monkeypatch):
    def _stub(name, value):
        monkeypatch.setattr(pipeline, name, lambda *a, **k: value)
    return _stub


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------


def test_summary_is_the_funnel_across_every_batch(stub_pipeline):
    stub_pipeline("global_summary", FUNNEL)
    body = client.get("/dashboard/summary").json()
    assert body["ingested"] == 233
    assert body["golden_built"] == 233


# --------------------------------------------------------------------------
# batches
# --------------------------------------------------------------------------


def test_batches_lists_recent_batches_most_recent_first(stub_pipeline):
    stub_pipeline("recent_batches", [{
        "batch_id": BATCH,
        "status": "running",
        "source_name": "vendor_big",
        "file_name": "ten_million.csv",
        "entity_type": "person",
        "rows_read": 4_213_009,
        "rows_ingested": 4_213_005,
        "rows_failed": 4,
        "started_at": NOW,
        "finished_at": None,
    }])
    body = client.get("/dashboard/batches").json()
    assert body["count"] == 1
    row = body["results"][0]
    # rows_read is what a still-running batch's progress is watched through.
    assert row["status"] == "running"
    assert row["rows_read"] == 4_213_009
    assert row["finished_at"] is None


def test_batches_limit_is_bounded():
    resp = client.get("/dashboard/batches?limit=0")
    assert resp.status_code == 422
    resp = client.get("/dashboard/batches?limit=501")
    assert resp.status_code == 422


# --------------------------------------------------------------------------
# one batch
# --------------------------------------------------------------------------


def test_one_batch_carries_its_own_funnel(stub_pipeline):
    stub_pipeline("batch_detail", {
        "batch_id": BATCH,
        "status": "completed",
        "source_name": "people_test",
        "file_name": "new test.xlsx",
        "entity_type": "person",
        "rows_read": 233,
        "rows_ingested": 233,
        "rows_failed": 0,
        "started_at": NOW,
        "finished_at": NOW,
        "error_message": None,
        "funnel": FUNNEL,
    })
    body = client.get(f"/dashboard/batches/{BATCH}").json()
    assert body["batch_id"] == BATCH
    assert body["funnel"]["validated"]["valid"] == 144


def test_an_unknown_batch_is_a_404(stub_pipeline):
    stub_pipeline("batch_detail", None)
    resp = client.get(f"/dashboard/batches/{BATCH}")
    assert resp.status_code == 404
    assert BATCH in resp.json()["detail"]


# --------------------------------------------------------------------------
# the console itself
# --------------------------------------------------------------------------


def test_the_dashboard_page_is_served_without_a_key(monkeypatch):
    """The page is a shell; the fetches it makes carry the key."""
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setenv("PCDF_API_KEYS", "a-real-key")
    resp = client.get("/dashboard/page")
    assert resp.status_code == 200
    assert "Pipeline dashboard" in resp.text


def test_the_dashboard_data_is_not_served_without_a_key(monkeypatch):
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setenv("PCDF_API_KEYS", "a-real-key")
    assert client.get("/dashboard/summary").status_code == 401
    assert client.get("/dashboard/batches").status_code == 401
    assert client.get(f"/dashboard/batches/{BATCH}").status_code == 401


# --------------------------------------------------------------------------
# the combined admin page
# --------------------------------------------------------------------------


def test_the_admin_page_is_served_without_a_key(monkeypatch):
    """A shell over both /review/* and /dashboard/*, same as each on its own."""
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setenv("PCDF_API_KEYS", "a-real-key")
    resp = client.get("/admin/page")
    assert resp.status_code == 200
    assert "Admin dashboard" in resp.text
