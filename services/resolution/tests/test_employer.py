"""What counts as an employer worth making an entity of."""

from resolution.employer import employer_identity


def test_a_name_with_a_domain_identifies_an_employer():
    name, corroborated = employer_identity({
        "company_name": ["UnitedHealth Group"],
        "website": ["unitedhealthgroup.com"],
    })
    assert name == "UnitedHealth Group"
    assert corroborated is True


def test_a_bare_name_does_not_identify_an_employer():
    """'Consulting' appears thousands of times meaning thousands of companies.

    A name is a moderate key precisely because it does not identify, so on its
    own it must not create an entity that every other vaguely-named employer
    will then collide with.
    """
    name, corroborated = employer_identity({"company_name": ["Consulting"]})
    assert name == "Consulting"
    assert corroborated is False


def test_no_company_name_means_no_employer():
    assert employer_identity({"phone": ["+15551234567"]}) == ("", False)


def test_empty_values_mean_no_employer():
    assert employer_identity({}) == ("", False)


def test_placeholders_are_not_employers():
    """A row saying someone is 'Self Employed' is saying they have no employer.

    Making an entity of it would merge every self-employed person's employer
    into a single organisation.
    """
    for placeholder in ("Self Employed", "self-employed", "Freelance",
                        "Retired", "Unemployed", "N/A", "none", "Student"):
        name, _ = employer_identity({
            "company_name": [placeholder], "website": ["x.com"],
        })
        assert name == "", placeholder


def test_a_real_name_is_taken_even_if_a_placeholder_comes_first():
    name, corroborated = employer_identity({
        "company_name": ["N/A", "Kaiser Permanente"],
        "linkedin_url": ["linkedin.com/company/kaiser"],
    })
    assert name == "Kaiser Permanente"
    assert corroborated is True


def test_each_corroborating_field_is_enough_on_its_own():
    for field, value in (
        ("website", "acme.com"),
        ("linkedin_url", "linkedin.com/company/acme"),
        ("company_external_id", "src:12345"),
        ("phone", "+15551234567"),
        ("address_line1", "88 Pine St"),
    ):
        _, corroborated = employer_identity({
            "company_name": ["Acme"], field: [value],
        })
        assert corroborated is True, field


def test_a_field_that_does_not_identify_is_not_corroboration():
    """Headcount and founding year describe a company; they do not pick one out."""
    _, corroborated = employer_identity({
        "company_name": ["Acme"],
        "employee_count": ["1200"],
        "founded_year": ["1994"],
    })
    assert corroborated is False
