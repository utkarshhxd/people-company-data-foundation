"""What the console does when nobody has configured a key, and when somebody has."""

import pytest
from fastapi.testclient import TestClient
from review_console import review
from review_console.auth import ENV_ALLOW_UNAUTH, ENV_KEYS
from review_console.main import app

SUMMARY = {
    "queues": {
        "mappings": {"open": 0, "oldest_seconds": 0.0},
        "candidates": {"open": 0, "oldest_seconds": 0.0},
        "quarantine": {"open": 0, "oldest_seconds": 0.0},
        "enrichment": {"open": 0, "oldest_seconds": 0.0},
    },
    "total_open": 0,
}


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ENV_KEYS, raising=False)
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)


@pytest.fixture(autouse=True)
def _stub_summary(monkeypatch):
    """The query is not under test here; the gate in front of it is."""
    monkeypatch.setattr(review, "queue_summary", lambda: SUMMARY)


def client(host: str = "testclient") -> TestClient:
    return TestClient(app, client=(host, 50000))


# --------------------------------------------------------------------------
# no keys configured
# --------------------------------------------------------------------------

def test_loopback_is_allowed_with_no_keys():
    """Local-first has to keep working without ceremony."""
    response = client("127.0.0.1").get("/review/summary")
    assert response.status_code == 200


def test_off_box_is_refused_with_no_keys():
    """The failure mode that matters: reachable, unconfigured, serving anyway.

    503 rather than 401, because the caller has done nothing wrong -- the
    deployment is the thing that is not ready.
    """
    response = client("10.0.0.5").get("/review/summary")
    assert response.status_code == 503
    assert ENV_KEYS in response.json()["detail"]


def test_unauthenticated_can_be_chosen_deliberately(monkeypatch):
    monkeypatch.setenv(ENV_ALLOW_UNAUTH, "true")
    assert client("10.0.0.5").get("/review/summary").status_code == 200


# --------------------------------------------------------------------------
# keys configured
# --------------------------------------------------------------------------

def test_a_valid_key_is_accepted(monkeypatch):
    monkeypatch.setenv(ENV_KEYS, "secret-one")
    response = client().get("/review/summary", headers={"X-API-Key": "secret-one"})
    assert response.status_code == 200


def test_bearer_is_accepted_too(monkeypatch):
    monkeypatch.setenv(ENV_KEYS, "secret-one")
    response = client().get(
        "/review/summary", headers={"Authorization": "Bearer secret-one"}
    )
    assert response.status_code == 200


def test_any_configured_key_works(monkeypatch):
    """Several keys so one can be rotated out without downtime."""
    monkeypatch.setenv(ENV_KEYS, "old-key, new-key")
    for key in ("old-key", "new-key"):
        assert client().get(
            "/review/summary", headers={"X-API-Key": key}
        ).status_code == 200


def test_a_missing_key_is_rejected(monkeypatch):
    monkeypatch.setenv(ENV_KEYS, "secret-one")
    response = client().get("/review/summary")
    assert response.status_code == 401


def test_a_wrong_key_is_rejected(monkeypatch):
    monkeypatch.setenv(ENV_KEYS, "secret-one")
    assert client().get(
        "/review/summary", headers={"X-API-Key": "nope"}
    ).status_code == 401


def test_loopback_does_not_bypass_a_configured_key(monkeypatch):
    """The local exemption covers an unconfigured console, never a configured one.

    Otherwise anything running on the same host -- including whatever else the
    developer has listening -- would have unauthenticated access to every record.
    """
    monkeypatch.setenv(ENV_KEYS, "secret-one")
    assert client("127.0.0.1").get("/review/summary").status_code == 401


def test_the_rejection_does_not_say_why(monkeypatch):
    """Useful to an attacker, useless to a caller who has exactly one key."""
    monkeypatch.setenv(ENV_KEYS, "secret-one")
    detail = client().get(
        "/review/summary", headers={"X-API-Key": "nope"}
    ).json()["detail"]
    assert "nope" not in detail
    assert "secret-one" not in detail


# --------------------------------------------------------------------------
# health and the console shell stay open
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/health/live", "/review/page"])
def test_operational_endpoints_never_require_a_key(monkeypatch, path):
    """A liveness probe that needs a credential reports the credential's health.

    Orchestrators check these before secrets are necessarily in place, and
    monitoring that dies with its own configuration is useless exactly when it
    is needed. The console shell is a shell too: gating it would mean a
    browser could never reach the point of being asked for a key.
    """
    monkeypatch.setenv(ENV_KEYS, "secret-one")
    assert client("10.0.0.5").get(path).status_code == 200
