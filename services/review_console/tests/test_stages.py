"""Route-level tests for the stage-runner.

`stages._RUNNERS` is stubbed with fake, instant functions so these never touch
a real database -- what's under test is the HTTP contract and the registry:
that a start returns immediately, that the registry reflects completion once
the (fake) function returns, that a second start while one is running is a
409, and that a bad stage name never reaches the registry at all.

Whether `map_batch`/`normalize_batch`/etc. themselves behave correctly is
each stage's own test suite's job, not this one's -- same division test_review.py
draws around `decisions.py`.
"""

import time

import pytest
from fastapi.testclient import TestClient
from review_console import stages
from review_console.auth import ENV_ALLOW_UNAUTH
from review_console.main import app

client = TestClient(app)

BATCH = "019ff9ac-dc79-749b-9b84-6e5e19fc03a4"


@pytest.fixture(autouse=True)
def _serve_without_a_key(monkeypatch):
    monkeypatch.setenv(ENV_ALLOW_UNAUTH, "true")


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is module-level and process-lifetime; tests must not leak
    run state for BATCH into each other."""
    yield
    with stages._lock:
        for stage in stages.STAGES:
            stages._runs.pop((stage, BATCH), None)


def _wait_until_not_running(stage: str, batch_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = stages.status_for(batch_id)[stage]
        if state is not None and state["status"] != "running":
            return state
        time.sleep(0.01)
    raise TimeoutError(f"{stage} for {batch_id} never left 'running'")


def test_starting_a_stage_reports_running_immediately(monkeypatch):
    started = []

    def slow(batch_id):
        started.append(batch_id)
        time.sleep(0.05)
        return {"ok": True}

    monkeypatch.setitem(stages._RUNNERS, "normalization", slow)
    resp = client.post(f"/dashboard/batches/{BATCH}/stages/normalization/run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert started == [BATCH]


def test_the_registry_reflects_completion(monkeypatch):
    monkeypatch.setitem(stages._RUNNERS, "golden", lambda batch_id: {"written": 4})
    client.post(f"/dashboard/batches/{BATCH}/stages/golden/run")
    state = _wait_until_not_running("golden", BATCH)
    assert state["status"] == "completed"
    assert state["detail"] == {"written": 4}
    assert state["error"] is None


def test_a_failed_run_carries_its_exact_message(monkeypatch):
    def boom(batch_id):
        raise ValueError(f"batch {batch_id} has no attribute observations; normalize it first")

    monkeypatch.setitem(stages._RUNNERS, "validation", boom)
    client.post(f"/dashboard/batches/{BATCH}/stages/validation/run")
    state = _wait_until_not_running("validation", BATCH)
    assert state["status"] == "failed"
    assert "normalize it first" in state["error"]


def test_starting_the_same_stage_twice_while_running_is_a_conflict(monkeypatch):
    def slow(batch_id):
        time.sleep(0.3)

    monkeypatch.setitem(stages._RUNNERS, "resolution", slow)
    first = client.post(f"/dashboard/batches/{BATCH}/stages/resolution/run")
    assert first.status_code == 200
    second = client.post(f"/dashboard/batches/{BATCH}/stages/resolution/run")
    assert second.status_code == 409
    assert "already running" in second.json()["detail"]
    _wait_until_not_running("resolution", BATCH)


def test_an_unknown_stage_name_never_reaches_the_registry():
    resp = client.post(f"/dashboard/batches/{BATCH}/stages/nonsense/run")
    assert resp.status_code == 422
    assert stages.status_for(BATCH)["mapping"] is None


def test_stage_run_is_not_served_without_a_key(monkeypatch):
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setenv("PCDF_API_KEYS", "a-real-key")
    resp = client.post(f"/dashboard/batches/{BATCH}/stages/mapping/run")
    assert resp.status_code == 401
