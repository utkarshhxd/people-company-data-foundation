"""Route-level tests for the entity browser.

`entities.py` is stubbed, same pattern as `test_review.py` stubs `review.py`.
What's under test is the HTTP contract: shapes, that a missing entity/field is
a 404, and that every route is key-gated like the rest of the console.

Whether `common.lineage`'s queries themselves are correct is that module's own
concern -- it already backs the `golden explain` CLI, and this only proves the
routes hand back what it returns.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from review_console import entities
from review_console.auth import ENV_ALLOW_UNAUTH
from review_console.main import app

client = TestClient(app)

NOW = datetime(2026, 8, 21, tzinfo=UTC)
ENTITY = "019ff9ac-dc79-749b-9b84-6e5e19fc03a4"


@pytest.fixture(autouse=True)
def _serve_without_a_key(monkeypatch):
    monkeypatch.setenv(ENV_ALLOW_UNAUTH, "true")


@pytest.fixture
def stub_entities(monkeypatch):
    def _stub(name, value):
        monkeypatch.setattr(entities, name, lambda *a, **k: value)
    return _stub


def test_search_returns_findable_entities(stub_entities):
    stub_entities("search", [
        {"entity_id": ENTITY, "entity_type": "person",
         "display_name": "Ada Lovelace", "record_count": 3},
    ])
    body = client.get("/entities/search?q=ada").json()
    assert body["count"] == 1
    assert body["results"][0]["display_name"] == "Ada Lovelace"


def test_search_requires_a_query():
    resp = client.get("/entities/search")
    assert resp.status_code == 422


def test_entity_detail_carries_attributes_and_relationships(stub_entities):
    stub_entities("detail", {
        "entity_id": ENTITY,
        "requested_entity_id": ENTITY,
        "was_merged": False,
        "entity_type": "person",
        "created_at": NOW,
        "attributes": {
            "full_name": {"canonical_field": "full_name", "value": "Ada Lovelace",
                           "confidence": 0.95, "strategy": "majority",
                           "supporting_sources": 2, "competing_values": 0,
                           "valid_from": NOW},
        },
        "observed_by": [{"source_name": "vendor_x", "file_name": "x.csv",
                          "row_number": 3, "match_method": "email",
                          "match_confidence": 0.95, "match_status": "auto_linked",
                          "record_id": ENTITY}],
        "relationships": [{"relationship_type": "employed_at", "direction": "employer",
                            "entity_id": ENTITY, "entity_type": "company",
                            "display_name": "Analytical Engines Ltd"}],
    })
    body = client.get(f"/entities/{ENTITY}").json()
    assert body["attributes"]["full_name"]["value"] == "Ada Lovelace"
    assert body["relationships"][0]["display_name"] == "Analytical Engines Ltd"


def test_an_unknown_entity_is_a_404(stub_entities):
    stub_entities("detail", None)
    resp = client.get(f"/entities/{ENTITY}")
    assert resp.status_code == 404
    assert ENTITY in resp.json()["detail"]


def test_field_explain_shows_every_contribution(stub_entities):
    stub_entities("explain", {
        "entity_id": ENTITY,
        "canonical_field": "email",
        "golden": {"value": "ada@example.com", "confidence": 0.95},
        "winning_record_id": ENTITY,
        "contributions": [
            {"source_name": "vendor_x", "raw_value": "Ada@Example.com",
             "normalized_value": "ada@example.com", "agrees_with_golden": True,
             "is_winning_record": True, "validation": []},
        ],
        "history": [],
    })
    body = client.get(f"/entities/{ENTITY}/fields/email").json()
    assert body["contributions"][0]["is_winning_record"] is True


def test_explaining_an_unknown_field_is_a_404(stub_entities):
    stub_entities("explain", None)
    resp = client.get(f"/entities/{ENTITY}/fields/nonsense")
    assert resp.status_code == 404


def test_timeline_wraps_the_event_list(stub_entities):
    stub_entities("timeline", [
        {"at": NOW, "event": "record_ingested", "actor": "vendor_x", "detail": "x.csv row 3"},
    ])
    body = client.get(f"/entities/{ENTITY}/timeline").json()
    assert body["count"] == 1
    assert body["events"][0]["event"] == "record_ingested"


def test_entities_are_not_served_without_a_key(monkeypatch):
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setenv("PCDF_API_KEYS", "a-real-key")
    assert client.get("/entities/search?q=ada").status_code == 401
    assert client.get(f"/entities/{ENTITY}").status_code == 401
