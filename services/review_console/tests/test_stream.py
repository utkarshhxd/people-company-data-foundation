"""Tests for the push dashboard stream.

Two layers, deliberately kept apart:

- The HTTP route (`GET /dashboard/stream`) is only proven at the auth
  boundary here. Starlette's `TestClient` runs a streaming ASGI response
  through a background-thread portal, and entering its streaming context
  manager blocks waiting for the response to progress -- fine for a request
  that finishes, not for a connection this endpoint holds open on purpose.
  Fighting that transport plumbing to unit-test an intentionally-infinite
  stream isn't worth it; the 401 case still exercises the real route because
  `require_api_key` raises before any streaming ever starts.
- Everything about what a connection actually receives -- the immediate
  snapshot, a batch_id adding that batch's detail -- is tested against
  `stream.events()` directly, the plain async generator FastAPI wraps in a
  `StreamingResponse`. Calling it without going through the ASGI transport
  is both simpler and the only way to bound it: each test asks for exactly
  one frame and stops, rather than trusting a client library to close an
  unbounded connection promptly.

The poller's periodic broadcast (`_poll_forever`) is timing-based background
scheduling; proving it live against the running service, as the plan this
shipped from did, is a better use of effort than simulating asyncio's clock.
"""

import json

import pytest
from fastapi.testclient import TestClient
from review_console import pipeline, review, stream
from review_console.auth import ENV_ALLOW_UNAUTH
from review_console.main import app

client = TestClient(app)

FUNNEL = {
    "ingested": 10, "failed": 0, "normalized": 10,
    "validated": {"valid": 10, "warning": 0, "invalid": 0},
    "quarantine": {"open": 0, "released": 0, "rejected": 0, "resolved": 0},
    "resolved": {"auto_linked": 0, "new_entity": 10, "manual": 0},
    "golden_built": 10, "needs_review": {"mapping": 0, "candidates": 0},
}
QUEUES = {
    "queues": {
        "mappings": {"open": 0, "oldest_seconds": 0.0},
        "candidates": {"open": 0, "oldest_seconds": 0.0},
        "quarantine": {"open": 0, "oldest_seconds": 0.0},
        "enrichment": {"open": 0, "oldest_seconds": 0.0},
    },
    "total_open": 0,
}


@pytest.fixture(autouse=True)
def _serve_without_a_key(monkeypatch):
    monkeypatch.setenv(ENV_ALLOW_UNAUTH, "true")


@pytest.fixture(autouse=True)
def _stub_snapshot_sources(monkeypatch):
    monkeypatch.setattr(pipeline, "global_summary", lambda: FUNNEL)
    monkeypatch.setattr(pipeline, "recent_batches", lambda limit=50: [])
    monkeypatch.setattr(review, "queue_summary", lambda: QUEUES)


def test_stream_is_not_served_without_a_key(monkeypatch):
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setenv("PCDF_API_KEYS", "a-real-key")
    resp = client.get("/dashboard/stream")
    assert resp.status_code == 401


async def _first_frame(batch_id: str | None = None) -> dict:
    agen = stream.events(batch_id)
    try:
        text = await agen.__anext__()
    finally:
        await agen.aclose()
    assert text.startswith("data: ") and text.endswith("\n\n")
    return json.loads(text[len("data: "):])


@pytest.mark.anyio
async def test_the_first_frame_is_an_immediate_snapshot_no_batch_selected():
    frame = await _first_frame()
    assert frame["summary"] == FUNNEL
    assert frame["batches"] == []
    assert frame["queues"] == QUEUES
    assert "batch" not in frame


@pytest.mark.anyio
async def test_a_batch_id_adds_its_detail_to_the_frame(monkeypatch):
    detail = {"batch_id": "b1", "status": "completed", "funnel": FUNNEL}
    monkeypatch.setattr(pipeline, "batch_detail", lambda batch_id: detail)
    frame = await _first_frame("b1")
    assert frame["batch"] == detail


@pytest.mark.anyio
async def test_closing_the_generator_drops_the_subscriber():
    agen = stream.events(None)
    await agen.__anext__()
    assert len(stream._subscribers) == 1
    await agen.aclose()
    assert len(stream._subscribers) == 0
