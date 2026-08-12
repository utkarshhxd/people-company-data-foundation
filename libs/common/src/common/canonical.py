"""The canonical schema: the vocabulary every source is mapped onto.

Sources use arbitrary column names (e_mail, mobile_no, org_name, designation).
These definitions are the target of that mapping. Aliases are the accumulated
knowledge of what vendors actually call things.

Two kinds of alias:
  aliases            - unambiguous; an exact match is trustworthy on its own.
  ambiguous_aliases  - plausible but not certain (e.g. "location" usually means
                       city, but sometimes a full address). These propose the
                       field and then force human review rather than
                       auto-accepting, so a coin-flip never becomes truth.

Bump CANONICAL_SCHEMA_VERSION when fields change meaning, so a stored mapping
always records which vocabulary it was made against.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field

CANONICAL_SCHEMA_VERSION = "2"

PERSON = "person"
COMPANY = "company"


@dataclass(frozen=True)
class CanonicalField:
    name: str
    entity_type: str
    description: str
    aliases: frozenset[str] = field(default_factory=frozenset)
    ambiguous_aliases: frozenset[str] = field(default_factory=frozenset)
    # Name of a value detector that corroborates this field, when one applies.
    detector: str | None = None
    # How a value of this field should be normalized (see normalization service).
    value_type: str = "text"


def _f(name, entity_type, description, aliases=(), detector=None,
       value_type="text", ambiguous=()) -> CanonicalField:
    return CanonicalField(
        name, entity_type, description, frozenset(aliases), frozenset(ambiguous),
        detector, value_type,
    )


PERSON_FIELDS: tuple[CanonicalField, ...] = (
    _f("full_name", PERSON, "Complete personal name as given",
       ("name", "fullname", "contact_name", "person_name", "display_name", "full name"),
       value_type="person_name"),
    _f("first_name", PERSON, "Given name",
       ("fname", "first", "given_name", "forename", "firstname"),
       value_type="person_name"),
    _f("middle_name", PERSON, "Middle name or initial", ("mname", "middle", "middlename"),
       value_type="person_name"),
    _f("last_name", PERSON, "Family name",
       ("lname", "last", "surname", "family_name", "lastname"),
       value_type="person_name"),
    _f("email", PERSON, "Email address",
       ("e_mail", "email_address", "emailaddress", "mail", "email_id", "primary_email",
        "work_email", "contact_email", "e mail"), detector="email", value_type="email"),
    _f("email_status", PERSON, "Source's verification state for the email",
       ("email_state", "email_validity", "email_verification", "email_verified",
        "email_quality"), value_type="lower_token"),
    _f("phone", PERSON, "Telephone number",
       ("mobile_no", "mobile", "mobile_number", "phone_number", "telephone", "tel",
        "contact_no", "contact_number", "phone_no", "cell", "cellphone", "mobile no",
        "phones", "phone_numbers"), detector="phone", value_type="phone"),
    _f("fax_phone", PERSON, "Fax number",
       ("fax", "fax_number", "fax_no", "facsimile"), detector="phone", value_type="phone"),
    _f("job_title", PERSON, "Role held at the employer",
       ("designation", "title", "position", "role", "job_role", "jobtitle")),
    _f("department", PERSON, "Department or function", ("dept", "division", "team")),
    _f("company_name", PERSON, "Employer name",
       ("org_name", "organisation", "organization", "employer", "company", "org",
        "account_name", "business_name", "company_name")),
    _f("industry", PERSON, "Industry the person works in",
       ("sector", "vertical", "industry_name", "industry_sector")),
    _f("linkedin_url", PERSON, "LinkedIn profile URL",
       ("linkedin", "linkedin_profile", "li_url", "linkedin_link"),
       detector="linkedin", value_type="url"),
    _f("profile_image_url", PERSON, "Profile photo URL",
       ("profile_pic", "profile_picture", "profile_photo", "photo", "avatar",
        "image_url", "picture"), value_type="url"),
    _f("address_line1", PERSON, "Street address, first line",
       ("house_address", "address", "street_address", "address1", "addr1", "street",
        "address_1"), value_type="address"),
    _f("address_line2", PERSON, "Street address, second line",
       ("address2", "addr2", "address_2", "apartment", "suite", "unit"),
       value_type="address"),
    _f("city", PERSON, "City or town", ("town", "locality"),
       value_type="place_name", ambiguous=("location", "city_state", "place")),
    _f("state_region", PERSON, "State, province, or region",
       ("state", "province", "region", "county"), value_type="region_code"),
    _f("postal_code", PERSON, "Postal or ZIP code",
       ("zip", "zipcode", "zip_code", "postcode", "post_code", "pincode", "pin_code"),
       detector="postal_code", value_type="postal_code"),
    _f("country", PERSON, "Country", ("country_name", "nation"),
       value_type="region_code"),
    _f("person_external_id", PERSON, "Source's own identifier for this person",
       ("external_id", "person_id", "contact_id", "record_id", "id"),
       value_type="identifier"),
)

COMPANY_FIELDS: tuple[CanonicalField, ...] = (
    _f("company_name", COMPANY, "Company name",
       ("org_name", "organisation", "organization", "company", "org", "account_name",
        "business_name", "name")),
    _f("legal_name", COMPANY, "Registered legal name",
       ("registered_name", "legal_entity_name", "entity_name")),
    _f("website", COMPANY, "Company website or domain",
       ("url", "web", "homepage", "site", "domain", "web_site", "company_website",
        "web_address", "website_url", "webaddress"), detector="url", value_type="url"),
    _f("email", COMPANY, "Company email address",
       ("e_mail", "email_address", "mail", "contact_email", "info_email"),
       detector="email", value_type="email"),
    _f("phone", COMPANY, "Company telephone number",
       ("phone_number", "telephone", "tel", "contact_no", "contact_number", "phone_no",
        "phones"), detector="phone", value_type="phone"),
    _f("fax_phone", COMPANY, "Company fax number",
       ("fax", "fax_number", "fax_no", "facsimile"), detector="phone", value_type="phone"),
    _f("address_line1", COMPANY, "Street address, first line",
       ("address", "street_address", "address1", "addr1", "street", "address_1",
        "house_address"), value_type="address"),
    _f("address_line2", COMPANY, "Street address, second line",
       ("address2", "addr2", "address_2", "suite", "unit"), value_type="address"),
    _f("city", COMPANY, "City or town", ("town", "locality"),
       value_type="place_name", ambiguous=("location", "place")),
    _f("state_region", COMPANY, "State, province, or region",
       ("state", "province", "region", "county"), value_type="region_code"),
    _f("postal_code", COMPANY, "Postal or ZIP code",
       ("zip", "zipcode", "zip_code", "postcode", "post_code", "pincode", "pin_code"),
       detector="postal_code", value_type="postal_code"),
    _f("country", COMPANY, "Country", ("country_name", "nation"),
       value_type="region_code"),
    _f("industry", COMPANY, "Industry or sector",
       ("sector", "vertical", "industry_name", "industry_sector")),
    _f("sic_code", COMPANY, "SIC classification code", ("sic", "sic_codes"),
       value_type="identifier"),
    _f("sic_description", COMPANY, "Human-readable SIC classification",
       ("sic_desc", "sic_description", "sic_text", "industry_description",
        "sic_industry")),
    _f("naics_code", COMPANY, "NAICS classification code", ("naics", "naics_codes"),
       value_type="identifier"),
    _f("employee_count", COMPANY, "Number of employees",
       ("employees", "headcount", "num_employees", "employee_size", "size",
        "company_size"), detector="integer", value_type="integer"),
    _f("founded_year", COMPANY, "Year the company was founded",
       ("founded", "year_founded", "established", "inception", "founding_year"),
       detector="year", value_type="integer"),
    _f("linkedin_url", COMPANY, "LinkedIn company page URL",
       ("linkedin", "linkedin_profile", "li_url"), detector="linkedin", value_type="url"),
    _f("company_external_id", COMPANY, "Source's own identifier for this company",
       ("external_id", "company_id", "account_id", "record_id", "id"),
       value_type="identifier"),
    _f("company_category", COMPANY, "Source's own category/segment code",
       ("cat", "category", "segment", "class", "company_class")),
)

FIELDS_BY_ENTITY: dict[str, tuple[CanonicalField, ...]] = {
    PERSON: PERSON_FIELDS,
    COMPANY: COMPANY_FIELDS,
}


def fields_for(entity_type: str) -> tuple[CanonicalField, ...]:
    try:
        return FIELDS_BY_ENTITY[entity_type]
    except KeyError:
        raise ValueError(f"unknown entity_type {entity_type!r}") from None


def field_by_name(entity_type: str, name: str) -> CanonicalField | None:
    for spec in fields_for(entity_type):
        if spec.name == name:
            return spec
    return None


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_column_name(name: str) -> str:
    """Fold a column name to a comparable form: 'E-Mail Address ' -> 'e_mail_address'."""
    return _NON_ALNUM.sub("_", name.strip().lower()).strip("_")


def column_fingerprint(columns: list[str]) -> str:
    """Identify 'this exact column layout'.

    Shared by mapping (which records the layout) and normalization (which looks
    up the mapping made for it), so both must compute it identically.
    """
    canonical = json.dumps(columns, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
