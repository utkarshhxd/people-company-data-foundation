import pytest
from resolution.keys import (
    MODERATE,
    STRONG,
    keys_for,
    linkedin_handle,
    website_domain,
)


def by_type(keys):
    return {k.key_type: k for k in keys}


@pytest.mark.parametrize(
    "url,expected",
    [
        ("asiafoundation.org", "asiafoundation.org"),
        ("www.asiafoundation.org", "asiafoundation.org"),
        ("https://www.asiafoundation.org/", "asiafoundation.org"),
        ("http://asiafoundation.org/about?utm_source=x", "asiafoundation.org"),
        ("https://user:pw@asiafoundation.org:8443/x", "asiafoundation.org"),
        ("localhost", None),
        ("", None),
    ],
)
def test_website_reduces_to_the_host_that_identifies_the_organisation(url, expected):
    """Scheme, www and path differ between vendors for plainly the same company."""
    assert website_domain(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.linkedin.com/company/the-asia-foundation",
         "company/the-asia-foundation"),
        ("linkedin.com/in/someone/", "in/someone"),
        ("https://www.linkedin.com/in/someone?trk=nav", "in/someone"),
        ("https://twitter.com/someone", None),
    ],
)
def test_linkedin_reduces_to_the_profile_path(url, expected):
    assert linkedin_handle(url) == expected


def test_a_vendors_own_id_is_scoped_to_that_vendor():
    """Two vendors reuse the same integers for entirely different entities."""
    a = keys_for("company", {"company_external_id": ["501"]}, "source-a")
    b = keys_for("company", {"company_external_id": ["501"]}, "source-b")
    assert a[0].key_value != b[0].key_value
    assert a[0].strength == STRONG


def test_company_website_and_email_are_strong_but_name_is_not():
    keys = by_type(keys_for(
        "company",
        {"company_name": ["The Asia Foundation"], "website": ["www.asiafoundation.org"],
         "email": ["taf@pk.asiafound.org"], "city": ["San Francisco"]},
        "src",
    ))
    assert keys["website_domain"].strength == STRONG
    assert keys["email"].strength == STRONG
    assert keys["name"].strength == MODERATE
    assert keys["name_city"].strength == MODERATE


def test_a_persons_email_is_strong_evidence():
    keys = by_type(keys_for("person", {"email": ["a.person@example.com"]}, "src"))
    assert keys["email"].strength == STRONG


def test_a_role_mailbox_is_demoted_for_a_person():
    """Everyone who shares info@ would otherwise collapse into one human being.

    Validation already judged this address to be a role account; resolution
    honours that verdict rather than re-deciding it.
    """
    keys = by_type(keys_for(
        "person", {"email": ["info@example.com"]}, "src", role_email=True
    ))
    assert keys["email"].strength == MODERATE


def test_a_phone_is_never_strong_for_either_entity_type():
    """A phone identifies a line. Colleagues share a switchboard."""
    person = by_type(keys_for("person", {"phone": ["+14153928863"]}, "src"))
    company = by_type(keys_for("company", {"phone": ["+14153928863"]}, "src"))
    assert person["phone"].strength == MODERATE
    assert company["phone"].strength == MODERATE


def test_first_and_last_name_stand_in_for_a_full_name():
    keys = by_type(keys_for(
        "person",
        {"first_name": ["Aruna"], "last_name": ["Rodrigues"], "company_name": ["Acme"]},
        "src",
    ))
    assert keys["name_company"].key_value == "Aruna Rodrigues|Acme"


def test_multiple_emails_all_become_keys():
    """A second address makes the entity findable one more way; dropping it would
    make a later record fail to match."""
    keys = keys_for("company", {"email": ["a@x.com", "b@x.com"]}, "src")
    assert {k.key_value for k in keys if k.key_type == "email"} == {"a@x.com", "b@x.com"}


def test_a_record_with_no_usable_field_produces_no_keys():
    assert keys_for("company", {"sic_description": ["Banking"]}, "src") == []


def test_duplicate_keys_are_collapsed():
    keys = keys_for("company", {"email": ["a@x.com", "a@x.com"]}, "src")
    assert len([k for k in keys if k.key_type == "email"]) == 1


def test_unknown_entity_type_is_refused():
    with pytest.raises(ValueError, match="unknown entity_type"):
        keys_for("robot", {}, "src")
