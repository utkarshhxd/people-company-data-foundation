"""Route-level tests.

The lineage queries are stubbed: what is under test here is the HTTP contract —
status codes, shapes, and that a missing entity is a 404 rather than a 200 with
an empty body. The joins themselves are exercised against a real database in the
end-to-end verification, because a stub cannot tell you whether a join is right.
"""

from datetime import UTC, datetime

import pytest
from api_service.auth import ENV_ALLOW_UNAUTH
from api_service.main import app
from common import lineage
from fastapi.testclient import TestClient

client = TestClient(app)

NOW = datetime(2026, 8, 13, tzinfo=UTC)
ENTITY = "019ff9ac-dc79-749b-9b84-6e5e19fc03a4"


@pytest.fixture(autouse=True)
def _serve_without_a_key(monkeypatch):
    """Declare that these tests are about the routes, not the gate in front.

    Without this every request here is refused before it reaches a handler,
    because TestClient does not present a loopback address and no keys are
    configured. Which is the correct behaviour -- test_auth.py covers it -- and
    the reason it is opted out of explicitly here rather than worked around.
    """
    monkeypatch.setenv(ENV_ALLOW_UNAUTH, "true")


@pytest.fixture
def stub(monkeypatch):
    def _stub(name, value):
        monkeypatch.setattr(lineage, name, lambda *a, **k: value)
    return _stub


def test_missing_entity_is_a_404_not_an_empty_success(stub):
    stub("entity_summary", None)
    resp = client.get(f"/entities/{ENTITY}")
    assert resp.status_code == 404
    assert ENTITY in resp.json()["detail"]


def test_entity_returns_its_trusted_values_and_who_observed_it(stub):
    stub("entity_summary", {
        "entity_id": ENTITY,
        "requested_entity_id": ENTITY,
        "was_merged": False,
        "entity_type": "company",
        "created_at": NOW,
        "attributes": {"company_name": {"value": "Asia Foundation", "confidence": 0.75}},
        "observed_by": [{"source_name": "vendor_dc"}, {"source_name": "vendor_b"}],
    })
    body = client.get(f"/entities/{ENTITY}").json()
    assert body["attributes"]["company_name"]["value"] == "Asia Foundation"
    assert len(body["observed_by"]) == 2


def test_a_merged_id_still_answers_and_says_so(stub):
    """A retired id must keep resolving; that is the promise of a stable id."""
    stub("entity_summary", {
        "entity_id": ENTITY,
        "requested_entity_id": "old-id",
        "was_merged": True,
        "entity_type": "company",
        "created_at": NOW,
        "attributes": {},
        "observed_by": [],
    })
    body = client.get("/entities/old-id").json()
    assert body["was_merged"] is True
    assert body["entity_id"] == ENTITY


def test_explain_keeps_the_confidence_dimensions_separate(stub):
    """Four different questions, four different numbers, never one blended score."""
    stub("explain_value", {
        "entity_id": ENTITY,
        "requested_entity_id": ENTITY,
        "canonical_field": "company_name",
        "golden": {"value": "Asia Foundation", "confidence": 0.75,
                   "strategy": "most_frequent"},
        "winning_record_id": "rec-1",
        "contributions": [{
            "source_name": "vendor_dc",
            "raw_value": "ASIA FOUNDATION",
            "normalized_value": "Asia Foundation",
            "mapping_confidence": 1.0,
            "match_confidence": 0.96,
            "source_reliability": 0.7,
            "record_validation_status": "warning",
            "validation": [],
            "agrees_with_golden": True,
            "is_winning_record": True,
        }],
        "history": [],
    })
    contribution = client.get(f"/entities/{ENTITY}/explain/company_name").json()["contributions"][0]
    assert contribution["mapping_confidence"] == 1.0
    assert contribution["match_confidence"] == 0.96
    assert contribution["source_reliability"] == 0.7
    assert contribution["record_validation_status"] == "warning"
    # The raw source cell is reachable from the trusted value.
    assert contribution["raw_value"] == "ASIA FOUNDATION"


def test_explaining_an_unknown_field_is_a_404(stub):
    stub("explain_value", None)
    assert client.get(f"/entities/{ENTITY}/explain/nonexistent").status_code == 404


def test_timeline_is_returned_with_a_count(stub):
    stub("entity_timeline", [
        {"at": NOW, "event": "record_ingested", "actor": "vendor_dc", "detail": "row 1"},
        {"at": NOW, "event": "golden_value_set", "actor": "vendor_dc", "detail": "x"},
    ])
    body = client.get(f"/entities/{ENTITY}/timeline").json()
    assert body["count"] == 2
    assert body["events"][0]["event"] == "record_ingested"


def test_search_accepts_only_known_entity_types():
    assert client.get("/entities?entity_type=robot").status_code == 422


def test_search_limit_is_bounded():
    """An unbounded limit is a trivial way to knock the service over."""
    assert client.get("/entities?limit=100000").status_code == 422


def test_record_lineage_reports_which_golden_values_it_won(stub):
    stub("record_lineage", {
        "record_id": "rec-1",
        "row_number": 1,
        "file_name": "real_company_sample.csv",
        "source_name": "vendor_dc",
        "validation_status": "warning",
        "quarantine_status": None,
        "entity_id": ENTITY,
        "observations": [{"source_column": "NAME", "raw_value": "ASIA FOUNDATION"}],
        "golden_values_won": [{"canonical_field": "company_name",
                               "value": "Asia Foundation"}],
    })
    body = client.get("/records/rec-1").json()
    assert body["golden_values_won"][0]["canonical_field"] == "company_name"
    assert body["quarantine_status"] is None


def test_missing_record_is_a_404(stub):
    stub("record_lineage", None)
    assert client.get("/records/nope").status_code == 404
