"""The commercial-profile fields, and the columns that were producing nothing.

Every column named here was already being captured. It just mapped to no
canonical field, so 1.7M observations were stored and then ignored — never
validated, never resolved on, never in a golden record.
"""

import pytest
from common.canonical import COMPANY, PERSON, fields_for
from mapping.engine import STATUS_AUTO_ACCEPTED, map_column


@pytest.mark.parametrize(
    "column,expected",
    [
        # Apollo's contact export, verbatim. These arrive on a PERSON row and
        # describe the employer, which is why the subject assertion below
        # matters as much as the field name.
        ("Annual Revenue", "annual_revenue"),
        ("Total Funding", "total_funding"),
        ("Latest Funding", "latest_funding_stage"),
        ("Latest Funding Amount", "latest_funding_amount"),
        ("Last Raised At", "last_funding_date"),
        ("Technologies", "technologies"),
        ("Keywords", "keywords"),
        ("SEO Description", "seo_description"),
        ("Number of Retail Locations", "retail_location_count"),
    ],
)
def test_apollo_commercial_columns_now_map(column, expected):
    mapping = map_column(column, PERSON, [])
    assert mapping.canonical_field == expected
    assert mapping.status == STATUS_AUTO_ACCEPTED


@pytest.mark.parametrize(
    "column",
    ["Annual Revenue", "Total Funding", "Technologies", "SEO Description"],
)
def test_they_are_attributed_to_the_employer_not_the_person(column):
    """A person has no revenue. Mapping these to the person would put company
    facts on the contact's golden record and lose them for the company."""
    assert map_column(column, PERSON, []).subject == "employer"


@pytest.mark.parametrize(
    "column,expected",
    [
        ("annual_revenue", "annual_revenue"),
        ("sales_volume", "annual_revenue"),
        ("tech_stack", "technologies"),
        ("meta_description", "seo_description"),
    ],
)
def test_company_files_reach_the_same_fields(column, expected):
    """A company export and a contact export must land on one vocabulary, or the
    same fact arrives twice under two names."""
    assert map_column(column, COMPANY, []).canonical_field == expected


def test_every_employer_field_names_a_real_company_field():
    """An employer field carries a name from the COMPANY vocabulary, because the
    value ends up on a company entity. One that names a company field which does
    not exist would create a golden attribute nothing else can describe."""
    company_names = {f.name for f in fields_for(COMPANY)}
    employer_names = {f.name for f in fields_for(PERSON) if f.subject == "employer"}
    assert employer_names <= company_names


def test_person_fields_are_not_shadowed_by_the_unqualified_aliases():
    """The commercial fields answer to unqualified column names, which the
    employer convention normally forbids. That is safe only while no person
    field answers to the same name — this test is what keeps it safe."""
    person_self_names = {
        name
        for f in fields_for(PERSON) if f.subject == "self"
        for name in f.match_names
    }
    commercial = {"annual_revenue", "revenue", "total_funding", "funding",
                  "technologies", "keywords", "seo_description",
                  "latest_funding", "last_raised_at"}
    assert not (commercial & person_self_names)


@pytest.mark.parametrize(
    "column",
    ["mobile_no", "mobile", "cell", "primary_phone", "first_phone",
     "business_phone", "phone_number", "contact_no"],
)
def test_a_company_file_reaches_phone_by_the_names_vendors_actually_use(column):
    """A small business ships one number and calls it whatever its CRM called it.

    These aliases existed on the person vocabulary and not the company one, so a
    company file headed `mobile_no` produced captured-but-uncanonical phone
    numbers — and the phone rules never ran on them, which is the part that
    matters: a number too short to dial passed in silence rather than failing.
    """
    mapping = map_column(column, COMPANY, [])
    assert mapping.canonical_field == "phone"
    assert mapping.status == STATUS_AUTO_ACCEPTED
