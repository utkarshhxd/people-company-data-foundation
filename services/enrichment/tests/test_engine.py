from enrichment.engine import (
    ELIGIBLE_FIELDS,
    MIN_CONFIDENCE,
    MIN_KNOWN_FIELDS,
    ask_for_field,
)


def test_eligible_fields_are_a_small_curated_allowlist():
    """See ADR 0017: a derived exclude-list once let address/revenue/funding/
    free-text fields through, and a live smoke test showed the target model
    fabricates those confidently. The allowlist is deliberately explicit --
    and deliberately smaller than that first fix: sic_description,
    company_category and seniority are all "the source's own" classification
    by canonical definition, not a fact with one right answer to infer."""
    assert ELIGIBLE_FIELDS["company"] == ("industry",)
    assert ELIGIBLE_FIELDS["person"] == ("industry", "department")


def test_ask_for_field_rejects_a_field_outside_the_allowlist():
    known = {"company_name": "Acme", "website": "acme.com"}
    assert ask_for_field("company", "annual_revenue", known) is None
    assert ask_for_field("company", "seo_description", known) is None
    assert ask_for_field("company", "employee_count", known) is None
    assert ask_for_field("company", "sic_description", known) is None
    assert ask_for_field("company", "company_category", known) is None
    assert ask_for_field("person", "seniority", known) is None
    assert ask_for_field("person", "email", known) is None
    assert ask_for_field("company", "not_a_real_field", known) is None


def test_ask_for_field_requires_more_than_a_bare_name():
    """A single known fact -- often just the entity's own name -- was enough
    for the model to answer confidently anyway in testing (see ADR 0017)."""
    assert ask_for_field("company", "industry", {"company_name": "Acme"}) is None
    assert len({"company_name": "Acme"}) < MIN_KNOWN_FIELDS


def test_ask_for_field_declines_below_zero_value(monkeypatch):
    monkeypatch.setattr(
        "enrichment.engine.generate_json",
        lambda prompt, system=None: {"value": None, "confidence": 0},
    )
    known = {"company_name": "Acme", "website": "acme.com"}
    assert ask_for_field("company", "industry", known) is None


def test_ask_for_field_declines_low_confidence(monkeypatch):
    monkeypatch.setattr(
        "enrichment.engine.generate_json",
        lambda prompt, system=None: {"value": "Retail", "confidence": MIN_CONFIDENCE - 0.1},
    )
    known = {"company_name": "Acme", "website": "acme.com"}
    assert ask_for_field("company", "industry", known) is None


def test_ask_for_field_accepts_confident_answer(monkeypatch):
    monkeypatch.setattr(
        "enrichment.engine.generate_json",
        lambda prompt, system=None: {"value": "Retail", "confidence": 0.8},
    )
    known = {"company_name": "Acme", "website": "acme.com"}
    result = ask_for_field("company", "industry", known)
    assert result == ("Retail", 0.8, "text")


def test_ask_for_field_returns_none_when_model_unreachable(monkeypatch):
    monkeypatch.setattr("enrichment.engine.generate_json", lambda prompt, system=None: None)
    known = {"company_name": "Acme", "website": "acme.com"}
    assert ask_for_field("company", "industry", known) is None
