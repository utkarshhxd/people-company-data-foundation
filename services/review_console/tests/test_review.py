"""Route-level tests for the four review queues.

The queries and the stage functions are stubbed. What is under test is the HTTP
contract the console depends on: shapes, the status code a refused decision gets,
that a decision is passed through to the stage that owns it with the arguments
the reviewer supplied, and that a decision cannot be made anonymously.

Whether a merge actually merges is resolution's test to run, not this one's.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from review_console import decisions, review
from review_console.auth import ENV_ALLOW_UNAUTH
from review_console.main import app

client = TestClient(app)

NOW = datetime(2026, 8, 18, tzinfo=UTC)
MAPPING = "019ff9ac-dc79-749b-9b84-6e5e19fc03a4"
CANDIDATE = "019ff9ac-dc79-749b-9b84-6e5e19fc03b5"
RECORD = "019ff9ac-dc79-749b-9b84-6e5e19fc03c6"
ENTITY_A = "019ff9ac-dc79-749b-9b84-6e5e19fc03d7"
ENTITY_B = "019ff9ac-dc79-749b-9b84-6e5e19fc03e8"
PROPOSAL = "019ff9ac-dc79-749b-9b84-6e5e19fc03f9"


@pytest.fixture(autouse=True)
def _serve_without_a_key(monkeypatch):
    monkeypatch.setenv(ENV_ALLOW_UNAUTH, "true")


@pytest.fixture
def stub_review(monkeypatch):
    def _stub(name, value):
        monkeypatch.setattr(review, name, lambda *a, **k: value)
    return _stub


@pytest.fixture
def spy(monkeypatch):
    """Replace a decision function and record exactly what it was called with."""
    calls = []

    def _spy(name, result=None, raises=None):
        def fake(*args, **kwargs):
            calls.append({"name": name, "args": args, "kwargs": kwargs})
            if raises is not None:
                raise raises
            return result
        monkeypatch.setattr(decisions, name, fake)
        return calls

    return _spy


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------


def test_summary_reports_depth_and_age_together(stub_review):
    stub_review("queue_summary", {
        "queues": {
            "mappings": {"open": 3, "oldest_seconds": 1_209_600.0},
            "candidates": {"open": 0, "oldest_seconds": 0.0},
            "quarantine": {"open": 2, "oldest_seconds": 90.0},
            "enrichment": {"open": 0, "oldest_seconds": 0.0},
        },
        "total_open": 5,
    })
    body = client.get("/review/summary").json()
    assert body["total_open"] == 5
    # Age is the number that reveals a stopped queue; a count of three never can.
    assert body["queues"]["mappings"]["oldest_seconds"] == 1_209_600.0


# --------------------------------------------------------------------------
# schema mapping
# --------------------------------------------------------------------------


def test_mapping_queue_carries_the_values_a_reviewer_decides_from(stub_review):
    stub_review("mapping_queue", [{
        "mapping_id": MAPPING,
        "source_name": "apollo",
        "source_column": "Corporate Phone",
        "entity_type": "person",
        "subject": "employer",
        "canonical_field": "phone",
        "mapping_method": "ambiguous_alias",
        "mapping_confidence": 0.72,
        "mapping_status": "needs_review",
        "source_schema_id": MAPPING,
        "created_at": NOW,
        "reviewed_at": None,
        "reviewed_by": None,
        "alternatives": [
            {"canonical_field": "work_phone", "method": "alias", "confidence": 0.68}
        ],
        "collision": None,
        "samples": ["+14155550142", "+14155550143"],
    }])
    body = client.get("/review/mappings").json()
    assert body["count"] == 1
    row = body["results"][0]
    assert row["samples"] == ["+14155550142", "+14155550143"]
    assert row["alternatives"][0]["canonical_field"] == "work_phone"


def test_approving_a_mapping_passes_the_reviewer_through(spy):
    calls = spy("decide_mapping", result=decisions.Decision(
        summary="column mapped to work_phone",
        detail={"mapping_id": MAPPING, "canonical_field": "work_phone"},
        follow_up=["Re-run normalization for the affected batch."],
    ))
    resp = client.post(
        f"/review/mappings/{MAPPING}",
        json={"canonical_field": "work_phone", "reviewed_by": "utkarsh"},
    )
    assert resp.status_code == 200
    assert resp.json()["follow_up"] == ["Re-run normalization for the affected batch."]
    assert calls[0]["args"] == (MAPPING, "work_phone", "utkarsh")


def test_rejecting_a_mapping_sends_a_null_field_not_a_missing_one(spy):
    calls = spy("decide_mapping", result=decisions.Decision(summary="unmapped"))
    client.post(f"/review/mappings/{MAPPING}", json={"reviewed_by": "utkarsh"})
    assert calls[0]["args"] == (MAPPING, None, "utkarsh")


def test_a_field_outside_the_vocabulary_is_a_conflict_not_a_crash(spy):
    spy("decide_mapping", raises=decisions.DecisionRefused("'nope' is not a canonical field"))
    resp = client.post(
        f"/review/mappings/{MAPPING}",
        json={"canonical_field": "nope", "reviewed_by": "utkarsh"},
    )
    # 409, not 500: the reviewer asked for something the data does not allow.
    assert resp.status_code == 409
    assert "not a canonical field" in resp.json()["detail"]


def test_a_decision_cannot_be_made_anonymously():
    resp = client.post(
        f"/review/mappings/{MAPPING}", json={"canonical_field": "phone"}
    )
    assert resp.status_code == 422


def test_an_empty_name_is_not_a_name():
    resp = client.post(
        f"/review/mappings/{MAPPING}",
        json={"canonical_field": "phone", "reviewed_by": ""},
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------
# match candidates
# --------------------------------------------------------------------------


def test_candidate_queue_returns_two_comparable_sides(stub_review):
    stub_review("candidate_queue", [{
        "candidate_id": CANDIDATE,
        "record_id": RECORD,
        "entity_type": "company",
        "match_method": "name_city",
        "match_confidence": 0.84,
        "status": "open",
        "created_at": NOW,
        "reviewed_at": None,
        "reviewed_by": None,
        "review_note": None,
        "source_name": "vendor_near",
        "file_name": "near_match_company.csv",
        "row_number": 1,
        "existing": {"entity_id": ENTITY_A,
                     "fields": {"company_name": ["Acme Corp"], "city": ["Austin"]}},
        "incoming": {"entity_id": ENTITY_B,
                     "fields": {"company_name": ["Acme Corporation"], "city": ["Austin"]}},
    }])
    row = client.get("/review/candidates").json()["results"][0]
    assert row["existing"]["fields"]["company_name"] == ["Acme Corp"]
    assert row["incoming"]["fields"]["company_name"] == ["Acme Corporation"]


def test_accepting_a_merge_reports_what_the_rebuild_did(spy):
    calls = spy("decide_candidate", result=decisions.Decision(
        summary=f"merged {ENTITY_B} into {ENTITY_A}; the absorbed id still resolves",
        detail={"surviving_entity_id": ENTITY_A,
                "golden_rebuilt": {"entities": 1, "written": 4, "refreshed": 2,
                                   "unchanged": 9, "retired": 1}},
    ))
    body = client.post(
        f"/review/candidates/{CANDIDATE}",
        json={"accept": True, "reviewed_by": "utkarsh", "note": "same company"},
    ).json()
    assert body["detail"]["golden_rebuilt"]["written"] == 4
    assert calls[0]["args"] == (CANDIDATE, True, "utkarsh", "same company")


def test_rejecting_a_match_keeps_both_entities(spy):
    calls = spy("decide_candidate", result=decisions.Decision(
        summary="rejected; both entities stay separate"))
    body = client.post(
        f"/review/candidates/{CANDIDATE}",
        json={"accept": False, "reviewed_by": "utkarsh"},
    ).json()
    assert "stay separate" in body["summary"]
    assert calls[0]["args"] == (CANDIDATE, False, "utkarsh", None)


def test_deciding_an_already_closed_candidate_is_a_conflict(spy):
    spy("decide_candidate",
        raises=decisions.DecisionRefused("candidate is already accepted"))
    resp = client.post(
        f"/review/candidates/{CANDIDATE}",
        json={"accept": True, "reviewed_by": "utkarsh"},
    )
    assert resp.status_code == 409


# --------------------------------------------------------------------------
# quarantine
# --------------------------------------------------------------------------


def test_quarantine_queue_shows_the_failures_holding_each_record(stub_review):
    stub_review("quarantine_queue", [{
        "record_id": RECORD,
        "batch_id": MAPPING,
        "status": "open",
        "reason_codes": ["email.malformed"],
        "quarantined_at": NOW,
        "reviewed_by": None,
        "review_note": None,
        "reviewed_at": None,
        "entity_type": "company",
        "source_name": "vendor_bad",
        "file_name": "invalid_company.csv",
        "row_number": 3,
        "failures": [{
            "rule_id": "email.malformed", "severity": "error",
            "message": "not an email address", "canonical_field": "email",
            "source_column": "Email", "raw_value": "not-an-email",
        }],
    }])
    row = client.get("/review/quarantine").json()["results"][0]
    assert row["failures"][0]["raw_value"] == "not-an-email"


def test_a_record_that_is_not_quarantined_is_a_404(stub_review):
    stub_review("quarantine_detail", None)
    resp = client.get(f"/review/quarantine/{RECORD}")
    assert resp.status_code == 404
    assert RECORD in resp.json()["detail"]


def test_releasing_a_record_reports_what_it_set_in_motion(spy):
    calls = spy("decide_quarantine", result=decisions.Decision(
        summary="open -> released; now visible to entity resolution",
        detail={"downstream": {
            "resolution": {"records": 1, "linked": 0, "created": 1, "candidates": 0},
            "golden": {"entities": 1, "written": 6, "refreshed": 0,
                       "unchanged": 0, "retired": 0},
        }},
    ))
    body = client.post(
        f"/review/quarantine/{RECORD}",
        json={"action": "release", "reviewed_by": "utkarsh", "note": "checked by hand"},
    ).json()
    # The point of doing it here: a released record used to sit in a state no
    # queue showed until somebody remembered to re-resolve its batch.
    assert body["detail"]["downstream"]["resolution"]["created"] == 1
    assert calls[0]["args"] == (RECORD, "release", "utkarsh", "checked by hand")


def test_an_unknown_quarantine_action_never_reaches_the_stage(spy):
    calls = spy("decide_quarantine", result=decisions.Decision(summary="unreachable"))
    resp = client.post(
        f"/review/quarantine/{RECORD}",
        json={"action": "destroy", "reviewed_by": "utkarsh"},
    )
    assert resp.status_code == 422
    assert calls == []


def test_releasing_an_already_released_record_is_a_conflict(spy):
    spy("decide_quarantine", raises=decisions.DecisionRefused("record is already released"))
    resp = client.post(
        f"/review/quarantine/{RECORD}",
        json={"action": "release", "reviewed_by": "utkarsh"},
    )
    assert resp.status_code == 409


# --------------------------------------------------------------------------
# AI enrichment proposals
# --------------------------------------------------------------------------


def test_enrichment_queue_shows_what_else_is_known_about_the_entity(stub_review):
    stub_review("enrichment_queue", [{
        "proposal_id": PROPOSAL,
        "entity_id": ENTITY_A,
        "entity_type": "company",
        "canonical_field": "industry",
        "proposed_value": "Software",
        "value_type": "text",
        "stated_confidence": 0.81,
        "model": "gemma3:4b",
        "status": "pending",
        "reviewed_by": None,
        "created_at": NOW,
        "known_values": {"company_name": "Acme Corp", "city": "Austin"},
    }])
    row = client.get("/review/enrichment").json()["results"][0]
    assert row["known_values"]["company_name"] == "Acme Corp"
    assert row["proposed_value"] == "Software"


def test_confirming_a_proposal_reports_the_golden_rebuild(spy):
    calls = spy("decide_enrichment", result=decisions.Decision(
        summary="accepted; written as observation 019ff9ac-...",
        detail={"proposal_id": PROPOSAL, "entity_id": ENTITY_A,
                "golden_rebuilt": {"entities": 1, "written": 1, "refreshed": 3,
                                   "unchanged": 5, "retired": 0}},
    ))
    body = client.post(
        f"/review/enrichment/{PROPOSAL}",
        json={"accept": True, "reviewed_by": "utkarsh"},
    ).json()
    assert body["detail"]["golden_rebuilt"]["written"] == 1
    assert calls[0]["args"] == (PROPOSAL, True, "utkarsh")


def test_declining_a_proposal_writes_nothing(spy):
    calls = spy("decide_enrichment", result=decisions.Decision(
        summary="declined; nothing was written"))
    body = client.post(
        f"/review/enrichment/{PROPOSAL}",
        json={"accept": False, "reviewed_by": "utkarsh"},
    ).json()
    assert "nothing was written" in body["summary"]
    assert calls[0]["args"] == (PROPOSAL, False, "utkarsh")


def test_deciding_an_already_decided_proposal_is_a_conflict(spy):
    spy("decide_enrichment",
        raises=decisions.DecisionRefused(f"no pending proposal {PROPOSAL}"))
    resp = client.post(
        f"/review/enrichment/{PROPOSAL}",
        json={"accept": True, "reviewed_by": "utkarsh"},
    )
    assert resp.status_code == 409


# --------------------------------------------------------------------------
# the console itself
# --------------------------------------------------------------------------


def test_the_console_is_served_without_a_key(monkeypatch):
    """The page is a shell; the fetches it makes carry the key.

    Gating the shell would mean a browser could never reach the point of being
    asked.
    """
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setenv("PCDF_API_KEYS", "a-real-key")
    resp = client.get("/review/page")
    assert resp.status_code == 200
    assert "Review queues" in resp.text


def test_the_queues_themselves_are_not_served_without_a_key(monkeypatch):
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setenv("PCDF_API_KEYS", "a-real-key")
    assert client.get("/review/summary").status_code == 401
    assert client.post(
        f"/review/quarantine/{RECORD}",
        json={"action": "release", "reviewed_by": "utkarsh"},
    ).status_code == 401
