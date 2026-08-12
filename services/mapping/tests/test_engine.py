import pytest

from mapping.engine import (
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
    # 'website' is not a person field, so the person vocabulary must not match it.
    assert map_column("website", "person", []).canonical_field != "website"
