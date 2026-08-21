import pytest
from mapping.engine import (
    AI_SUGGESTION_CAP,
    AUTO_ACCEPT_THRESHOLD,
    STATUS_AUTO_ACCEPTED,
    STATUS_NEEDS_REVIEW,
    STATUS_UNMAPPED,
    VALUE_ANALYSIS_CAP,
    map_column,
    map_columns,
)


@pytest.mark.parametrize(
    "column,expected",
    [
        # The exact renames the spec calls out.
        ("e_mail", "email"),
        ("mobile_no", "phone"),
        ("house_address", "address_line1"),
        ("org_name", "company_name"),
        ("designation", "job_title"),
        # Casing/punctuation variants fold to the same alias.
        ("E-Mail", "email"),
        ("Mobile No", "phone"),
        ("ZIP Code", "postal_code"),
    ],
)
def test_known_aliases_auto_accept(column, expected):
    mapping = map_column(column, "person", [])
    assert mapping.canonical_field == expected
    assert mapping.method == "exact_alias"
    assert mapping.confidence == 1.0
    assert mapping.status == STATUS_AUTO_ACCEPTED


def test_near_miss_uses_similarity():
    mapping = map_column("emial_adress", "person", [])
    assert mapping.canonical_field == "email"
    assert mapping.method == "similarity"
    assert mapping.status in (STATUS_AUTO_ACCEPTED, STATUS_NEEDS_REVIEW)


def test_value_analysis_alone_never_auto_accepts():
    """Values prove the column's type, not which field it is."""
    mapping = map_column(
        "primary_contact_thing", "person",
        ["a@b.com", "c@d.org", "e@f.net", "g@h.io"],
    )
    assert mapping.canonical_field == "email"
    assert mapping.method == "value_analysis"
    assert mapping.confidence <= VALUE_ANALYSIS_CAP
    assert mapping.status == STATUS_NEEDS_REVIEW


def test_unrecognised_column_is_unmapped_not_forced():
    mapping = map_column("internal_widget_code", "person", ["xyz", "abc"])
    assert mapping.canonical_field is None
    assert mapping.status == STATUS_UNMAPPED
    assert "reason" in mapping.evidence


def test_evidence_records_alternatives():
    mapping = map_column("e_mail", "person", [])
    assert "alternatives" in mapping.evidence


def test_collision_forces_review():
    """Two columns claiming the same field is ambiguous — neither wins silently."""
    mappings = map_columns(["email", "e_mail"], "person", {})
    assert {m.source_column for m in mappings} == {"email", "e_mail"}
    for mapping in mappings:
        assert mapping.canonical_field == "email"
        assert mapping.status == STATUS_NEEDS_REVIEW
        assert mapping.evidence["collision"]["canonical_field"] == "email"
        assert set(mapping.evidence["collision"]["competing_columns"]) == {"email", "e_mail"}


def test_no_collision_when_fields_differ():
    mappings = map_columns(["e_mail", "mobile_no"], "person", {})
    assert all("collision" not in m.evidence for m in mappings)
    assert all(m.status == STATUS_AUTO_ACCEPTED for m in mappings)


def test_ai_suggestion_disabled_by_default():
    """Without ai_mapping_enabled, an unrecognised column stays unmapped --
    no model call is attempted at all."""
    mapping = map_column("internal_widget_code", "person", ["xyz", "abc"])
    assert mapping.status == STATUS_UNMAPPED
    assert mapping.method != "ai_suggestion"


def test_ai_suggestion_fills_a_gap_but_never_auto_accepts(monkeypatch):
    monkeypatch.setattr("mapping.engine.settings.ai_mapping_enabled", True)
    monkeypatch.setattr(
        "mapping.engine.generate_json",
        lambda prompt, system=None: {"field": "job_title", "confidence": 0.95, "reason": "x"},
    )
    mapping = map_column("what_they_do", "person", [])
    assert mapping.canonical_field == "job_title"
    assert mapping.method == "ai_suggestion"
    assert mapping.confidence <= AI_SUGGESTION_CAP
    assert mapping.confidence < AUTO_ACCEPT_THRESHOLD
    assert mapping.status == STATUS_NEEDS_REVIEW


def test_ai_suggestion_never_overrides_a_deterministic_match(monkeypatch):
    """A well-mapped column must not even ask the model."""
    called = []
    monkeypatch.setattr("mapping.engine.settings.ai_mapping_enabled", True)
    monkeypatch.setattr(
        "mapping.engine.generate_json",
        lambda prompt, system=None: called.append(1) or {"field": "phone", "confidence": 0.9},
    )
    mapping = map_column("e_mail", "person", [])
    assert mapping.method == "exact_alias"
    assert not called


def test_ai_suggestion_low_stated_confidence_is_dropped(monkeypatch):
    monkeypatch.setattr("mapping.engine.settings.ai_mapping_enabled", True)
    monkeypatch.setattr(
        "mapping.engine.generate_json",
        lambda prompt, system=None: {"field": "job_title", "confidence": 0.1},
    )
    mapping = map_column("internal_widget_code", "person", [])
    assert mapping.method != "ai_suggestion"
    assert mapping.status == STATUS_UNMAPPED


def test_ai_suggestion_unreachable_model_falls_back(monkeypatch):
    monkeypatch.setattr("mapping.engine.settings.ai_mapping_enabled", True)
    monkeypatch.setattr("mapping.engine.generate_json", lambda prompt, system=None: None)
    mapping = map_column("internal_widget_code", "person", [])
    assert mapping.status == STATUS_UNMAPPED


def test_ambiguous_alias_beats_similarity_noise_but_still_needs_review():
    """'location' is usually a city, but string similarity used to call it 'country'.

    A curated ambiguous alias must outrank a fuzzy ratio, yet never auto-accept.
    """
    mapping = map_column("location", "person", [])
    assert mapping.canonical_field == "city"
    assert mapping.method == "ambiguous_alias"
    assert mapping.status == STATUS_NEEDS_REVIEW


def test_value_corroboration_cannot_auto_accept_an_ambiguous_alias():
    """Values prove a column's type, never which field an ambiguous name meant."""
    mapping = map_column("location", "company", ["Washington", "Madison", "Boston"])
    assert mapping.status == STATUS_NEEDS_REVIEW


def test_real_vendor_columns_map_to_their_own_fields():
    """Regression for the collisions the first real files exposed."""
    company = ["ADDRESS", "WEB_ADDRESS", "PHONE_NUMBER", "FAX_NUMBER", "SIC_DESCRIPTION"]
    got = {m.source_column: m.canonical_field for m in map_columns(company, "company", {})}
    assert got == {
        "ADDRESS": "address_line1",
        "WEB_ADDRESS": "website",
        "PHONE_NUMBER": "phone",
        "FAX_NUMBER": "fax_phone",
        "SIC_DESCRIPTION": "sic_description",
    }


def test_source_own_id_is_captured_as_external_id():
    """The source's own key is a strong signal for later entity resolution."""
    assert map_column("ID", "company", []).canonical_field == "company_external_id"
    assert map_column("id", "person", []).canonical_field == "person_external_id"


def test_company_entity_uses_company_vocabulary():
    mapping = map_column("website", "company", [])
    assert mapping.canonical_field == "website"
    assert mapping.subject == "self"


def test_a_website_on_a_person_row_belongs_to_the_employer():
    """A person does not have a website; the company they work for does.

    So the column still maps to the company field `website` — but as an
    attribute of a different subject, which is what sends it to the employer's
    entity rather than the person's.
    """
    mapping = map_column("website", "person", [])
    assert mapping.canonical_field == "website"
    assert mapping.subject == "employer"


def test_an_unqualified_column_belongs_to_the_person():
    """The employer must never take a column that did not say it was theirs."""
    for column in ("city", "state", "phone", "address"):
        assert map_column(column, "person", []).subject == "self"


def test_a_company_qualified_column_belongs_to_the_employer():
    for column, expected in (
        ("Company City", "city"),
        ("Company State", "state_region"),
        ("Company Phone", "phone"),
        ("Company Address", "address_line1"),
        ("# Employees", "employee_count"),
    ):
        mapping = map_column(column, "person", [])
        assert mapping.canonical_field == expected, column
        assert mapping.subject == "employer", column


def test_person_and_employer_can_claim_the_same_field_without_colliding():
    """'City' and 'Company City' are different facts, not competing answers."""
    got = {m.source_column: m for m in map_columns(["City", "Company City"], "person", {})}
    assert got["City"].canonical_field == "city"
    assert got["City"].subject == "self"
    assert got["City"].status == STATUS_AUTO_ACCEPTED
    assert got["Company City"].canonical_field == "city"
    assert got["Company City"].subject == "employer"
    assert got["Company City"].status == STATUS_AUTO_ACCEPTED


def test_a_vendor_shipping_six_phone_columns_keeps_all_six():
    """Apollo ships six. Collapsing them onto one field would lose five."""
    columns = ["First Phone", "Work Direct Phone", "Home Phone", "Mobile Phone",
               "Corporate Phone", "Other Phone", "Company Phone"]
    got = {m.source_column: (m.canonical_field, m.subject)
           for m in map_columns(columns, "person", {})}
    assert got["First Phone"] == ("phone", "self")
    assert got["Mobile Phone"] == ("mobile_phone", "self")
    assert got["Home Phone"] == ("home_phone", "self")
    assert got["Other Phone"] == ("other_phone", "self")
    assert got["Company Phone"] == ("phone", "employer")
    # Two names for the same idea genuinely contest one field.
    assert got["Work Direct Phone"][0] == "work_phone"
    assert got["Corporate Phone"][0] == "work_phone"


def test_shape_detector_alone_does_not_propose_a_field():
    """'These are integers' does not say which integer field.

    Every follower count, review count, rating and ad flag in a real vendor
    export is an integer. Proposing employee_count for all of them names a
    field arbitrarily, and a plausible-looking proposal is worse than none
    because a reviewer tends to accept it.
    """
    mapping = map_column("googlereviewscount", "company", ["1200", "845", "23110"])
    assert mapping.canonical_field != "employee_count"
    assert mapping.status == STATUS_UNMAPPED


def test_shape_detector_still_corroborates_a_name_match():
    """Values may confirm a name-based candidate even when they cannot originate one."""
    mapping = map_column("employee_size", "company", ["12", "400", "37"])
    assert mapping.canonical_field == "employee_count"


def test_discriminating_detector_may_still_propose():
    """A column of linkedin.com URLs belongs to a LinkedIn field whatever it is called.

    The column name here is deliberately one no alias or fuzzy match reaches,
    so the values are the only evidence there is.
    """
    mapping = map_column("social_page_2", "person",
                         ["https://linkedin.com/in/a", "https://linkedin.com/in/b"])
    assert mapping.canonical_field == "linkedin_url"
    assert mapping.method == "value_analysis"


def test_a_shape_guess_never_reaches_the_collision_at_all():
    """The stronger guarantee: the bogus claim is not made, so nothing contests.

    `domain_expiration` holds phone-shaped digits, but 'is phone-shaped' is not
    grounds to claim the phone field — so `phone` keeps its auto-accept without
    a collision ever having to be settled.
    """
    columns = ["phone", "domain_expiration"]
    samples = {
        "phone": ["19172316712", "12123121600"],
        "domain_expiration": ["20260415", "20271130"],
    }
    got = {m.source_column: m for m in map_columns(columns, "company", samples)}
    assert got["phone"].canonical_field == "phone"
    assert got["phone"].status == STATUS_AUTO_ACCEPTED
    assert "collision" not in got["phone"].evidence
    assert got["domain_expiration"].canonical_field != "phone"


def test_exact_alias_holds_the_field_against_a_value_based_claim():
    """When a collision does form, the better kind of evidence takes the field."""
    columns = ["website", "g_maps"]
    samples = {
        "website": ["https://gilbaneco.com"],
        "g_maps": ["https://maps.google.com/?cid=123"],
    }
    got = {m.source_column: m for m in map_columns(columns, "company", samples)}
    assert got["website"].status == STATUS_AUTO_ACCEPTED
    assert got["website"].evidence["collision"]["outcome"] == "held"
    assert got["website"].evidence["collision"]["beat"] == ["g_maps"]


def test_equally_good_claims_on_one_field_still_go_to_review():
    """The rule that matters is preserved: genuine ambiguity reaches a human."""
    got = {m.source_column: m for m in map_columns(["name", "org_name"], "company", {})}
    assert got["name"].status == STATUS_NEEDS_REVIEW
    assert got["org_name"].status == STATUS_NEEDS_REVIEW
    assert got["name"].evidence["collision"]["outcome"] == "contested"


def test_a_column_that_loses_a_collision_claims_nothing():
    """Losing means claiming no field — never being reassigned to another one.

    The values are still captured as observations, so nothing is lost; what
    must not happen is data being attributed to a field it did not win.
    """
    columns = ["website", "domain_nameserver"]
    samples = {
        "website": ["https://gilbaneco.com"],
        "domain_nameserver": ["ns1.cloudflare.com", "ns2.cloudflare.com"],
    }
    got = {m.source_column: m for m in map_columns(columns, "company", samples)}
    assert got["website"].canonical_field == "website"
    loser = got["domain_nameserver"]
    assert loser.canonical_field is None
    assert loser.status == STATUS_UNMAPPED
    assert loser.evidence["collision"]["outcome"] == "yielded"
    assert loser.evidence["collision"]["would_have_been"] == "website"
