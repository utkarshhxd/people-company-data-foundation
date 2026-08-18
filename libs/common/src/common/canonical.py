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

# Bumped from "3": the commercial-profile fields (revenue, funding, technologies,
# keywords, SEO description, retail locations) joined the vocabulary. Columns
# that previously mapped to nothing now map to something, so a mapping made
# against version 3 and one made against version 4 can legitimately disagree
# about the same file — which is the whole reason a stored mapping records the
# version it was made against.
CANONICAL_SCHEMA_VERSION = "4"

PERSON = "person"
COMPANY = "company"

# Whose attribute a column holds. A vendor person export routinely describes two
# subjects in one row — the person, and the company they work for — and
# 'Company City' is not a worse answer to `city` than 'City' is, it is an answer
# about somebody else.
SELF = "self"
EMPLOYER = "employer"


@dataclass(frozen=True)
class CanonicalField:
    name: str
    # The kind of record this field can be mapped on.
    entity_type: str
    description: str
    aliases: frozenset[str] = field(default_factory=frozenset)
    ambiguous_aliases: frozenset[str] = field(default_factory=frozenset)
    # Name of a value detector that corroborates this field, when one applies.
    detector: str | None = None
    # How a value of this field should be normalized (see normalization service).
    value_type: str = "text"
    # Whose attribute this is on the record carrying it.
    subject: str = SELF
    # The entity type the subject is. For a self field this is entity_type; for
    # an employer field on a person row it is 'company', because `name` is a
    # name in the company vocabulary and the value ends up on a company entity.
    describes: str = ""

    def __post_init__(self) -> None:
        if not self.describes:
            object.__setattr__(self, "describes", self.entity_type)

    @property
    def match_names(self) -> frozenset[str]:
        """Column names that may match this field by name.

        A self field answers to its own name: a column called `city` is the
        city. An employer field must not — `city` on a person row is the
        person's city, and an employer field claiming it too would make every
        such column a collision, costing the person their own address. Employer
        fields are reachable only through explicitly qualified aliases, which is
        why every one of them names the employer.
        """
        names = set(self.aliases)
        if self.subject == SELF:
            names.add(self.name)
        return frozenset(names)


def _f(name, entity_type, description, aliases=(), detector=None,
       value_type="text", ambiguous=(), subject=SELF, describes="") -> CanonicalField:
    return CanonicalField(
        name, entity_type, description, frozenset(aliases), frozenset(ambiguous),
        detector, value_type, subject, describes,
    )


def _employer(name, description, aliases=(), detector=None, value_type="text",
              ambiguous=()) -> CanonicalField:
    """A column on a PERSON row describing that person's employer.

    `name` is a field in the *company* vocabulary, because that is where the
    value ends up: the employer becomes a company entity and this value becomes
    one of its observations. Keeping the company's own field names means an
    employer captured from an Apollo person row and a company captured from a
    company file are the same shape, and resolve against each other.
    """
    return _f(name, PERSON, description, aliases, detector, value_type, ambiguous,
              subject=EMPLOYER, describes=COMPANY)


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
    # The unqualified phone column. A vendor that ships one phone column means
    # this one; the qualified fields below exist for vendors that ship several,
    # where collapsing them all onto `phone` would make five columns contest one
    # field and lose four of them.
    _f("phone", PERSON, "Telephone number",
       ("mobile_no", "mobile", "mobile_number", "phone_number", "telephone", "tel",
        "contact_no", "contact_number", "phone_no", "cell", "cellphone", "mobile no",
        "phones", "phone_numbers", "first_phone", "primary_phone"),
       detector="phone", value_type="phone"),
    _f("mobile_phone", PERSON, "Mobile number, where the source distinguishes it",
       ("mobile_phone", "cell_phone", "cellular", "mobile_tel", "personal_mobile"),
       detector="phone", value_type="phone"),
    _f("work_phone", PERSON, "Work or direct line, where the source distinguishes it",
       ("work_phone", "work_direct_phone", "direct_phone", "office_phone",
        "business_phone", "corporate_phone", "direct_dial"),
       detector="phone", value_type="phone"),
    _f("home_phone", PERSON, "Home number, where the source distinguishes it",
       ("home_phone", "personal_phone", "residential_phone"),
       detector="phone", value_type="phone"),
    _f("other_phone", PERSON, "A further number the source did not classify",
       ("other_phone", "alternate_phone", "secondary_phone", "phone2", "phone_2"),
       detector="phone", value_type="phone"),
    _f("fax_phone", PERSON, "Fax number",
       ("fax", "fax_number", "fax_no", "facsimile"), detector="phone", value_type="phone"),
    _f("job_title", PERSON, "Role held at the employer",
       ("designation", "title", "position", "role", "job_role", "jobtitle")),
    _f("department", PERSON, "Department or function", ("dept", "division", "team")),
    _f("industry", PERSON, "Industry the person works in",
       ("sector", "vertical", "industry_name", "industry_sector")),
    _f("seniority", PERSON, "Seniority band the source assigns",
       ("seniority_level", "level", "job_level")),
    _f("headline", PERSON, "Self-description or profile headline",
       ("tagline", "profile_headline", "summary")),
    _f("facebook_url", PERSON, "Facebook profile URL",
       ("facebook", "facebook_profile", "fb_url"), detector="url", value_type="url"),
    _f("twitter_url", PERSON, "Twitter/X profile URL",
       ("twitter", "twitter_profile", "x_url", "twitter_handle"),
       detector="url", value_type="url"),
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
       # `apollo_contact_id` is here because its company counterpart always was:
       # `Apollo Account Id` mapped and `Apollo Contact Id` did not, so the same
       # export handed us the employer's vendor key and threw the person's away.
       ("external_id", "person_id", "contact_id", "record_id", "id",
        "apollo_contact_id"),
       value_type="identifier"),

    # --- the employer named inside this row -----------------------------------
    # These carry *company* field names because the value ends up on a company
    # entity. An employer captured here and a company captured from a company
    # file are then the same shape, and resolve against each other: the Gilbane
    # in an Apollo person row is the Gilbane in a company export.
    #
    # Every alias is explicitly employer-qualified. An unqualified `city` on a
    # person row is the person's, and a vendor that meant otherwise has to say
    # so — guessing costs the person their own address.
    _employer("company_name", "Employer name",
              ("org_name", "organisation", "organization", "employer", "company",
               "org", "account_name", "business_name", "company_name",
               "employer_name")),
    _employer("website", "Employer website or domain",
              ("company_domain", "company_website", "company_url", "domain",
               "website", "employer_website"), detector="url", value_type="url"),
    _employer("linkedin_url", "Employer's LinkedIn company page",
              ("company_linkedin", "company_linkedin_url",
               "company_linkedin_profile", "organization_linkedin"),
              detector="linkedin", value_type="url"),
    _employer("phone", "Employer switchboard number",
              ("company_phone", "employer_phone", "office_number",
               "company_phone_number"), detector="phone", value_type="phone"),
    _employer("address_line1", "Employer street address",
              ("company_address", "employer_address", "company_street",
               "company_address_1"), value_type="address"),
    _employer("city", "Employer city", ("company_city", "employer_city"),
              value_type="place_name"),
    _employer("state_region", "Employer state or region",
              ("company_state", "company_region", "employer_state"),
              value_type="region_code"),
    _employer("country", "Employer country", ("company_country", "employer_country"),
              value_type="region_code"),
    _employer("postal_code", "Employer postal code",
              ("company_zip", "company_postal_code", "company_zipcode"),
              detector="postal_code", value_type="postal_code"),
    _employer("employee_count", "Employer headcount",
              ("employees", "num_employees", "company_size", "size", "headcount",
               "employee_size", "company_employees"),
              detector="integer", value_type="integer"),
    _employer("founded_year", "Year the employer was founded",
              ("founded", "company_founded", "year_founded", "company_founded_year"),
              detector="year", value_type="integer"),
    _employer("industry", "Employer's industry",
              ("company_industry", "employer_industry")),
    _employer("company_external_id", "Source's own identifier for the employer",
              ("company_id", "account_id", "apollo_account_id", "employer_id"),
              value_type="identifier"),

    # The employer's commercial profile, as shipped on a contact row.
    #
    # These break the employer-qualification convention above, and may: the
    # convention exists because an unqualified `city` on a person row is the
    # PERSON's city, so an employer field answering to it would cost the person
    # their own address. None of the fields below has a person counterpart to
    # lose to. `Annual Revenue` on a contact row is not ambiguous about whose
    # revenue it is — a person does not have one — so demanding the vendor write
    # `Company Annual Revenue` would reject the only spelling anyone ships.
    _employer("annual_revenue", "Employer's reported annual revenue",
              ("annual_revenue", "revenue", "company_revenue", "employer_revenue",
               "estimated_revenue"), value_type="money"),
    _employer("total_funding", "Total capital the employer has raised",
              ("total_funding", "funding", "total_raised", "company_funding"),
              value_type="money"),
    _employer("latest_funding_stage", "Type of the employer's most recent round",
              ("latest_funding", "last_funding_type", "funding_stage",
               "latest_funding_round")),
    _employer("latest_funding_amount", "Amount raised in the employer's latest round",
              ("latest_funding_amount", "last_funding_amount"), value_type="money"),
    _employer("last_funding_date", "When the employer's most recent round closed",
              ("last_raised_at", "last_funding_date", "latest_funding_date"),
              value_type="date"),
    _employer("retail_location_count", "Retail premises the employer operates",
              ("number_of_retail_locations", "retail_locations", "store_count"),
              detector="integer", value_type="integer"),
    _employer("technologies", "Technology stack detected on the employer's site",
              ("technologies", "tech_stack", "company_technologies")),
    _employer("keywords", "Descriptive keywords assigned to the employer",
              ("keywords", "company_keywords")),
    _employer("seo_description", "Meta description published on the employer's site",
              ("seo_description", "company_seo_description", "meta_description")),
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
    # The mobile-shaped aliases are here for the same reason they are on a
    # person: a small business ships one number and calls it whatever its CRM
    # called it. Without them a company file headed `mobile_no` produced 496
    # captured-but-uncanonical phone numbers, and — worse — the phone rules
    # never ran on them, so a number too short to dial passed in silence.
    # A company does not own a mobile in any meaningful sense; it owns a number,
    # and this is the field for it.
    _f("phone", COMPANY, "Company telephone number",
       ("phone_number", "telephone", "tel", "contact_no", "contact_number", "phone_no",
        "phones", "mobile_no", "mobile", "mobile_number", "mobile no", "cell",
        "cellphone", "phone_numbers", "first_phone", "primary_phone",
        "business_phone", "office_phone"),
       detector="phone", value_type="phone"),
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

    # --- commercial profile ---------------------------------------------------
    # Scale, funding and web presence. These were captured from day one and
    # attributed to nothing: 1.7M observations sat in `unmapped` because the
    # vocabulary had no word for them, so they were never validated, never
    # resolved on, and never reached a golden record. Storing a value is not the
    # same as being able to answer a question with it.
    _f("annual_revenue", COMPANY,
       "Reported annual revenue, in whatever currency the source used",
       ("revenue", "annual_revenue", "yearly_revenue", "turnover", "annual_sales",
        "sales_volume", "estimated_revenue"), value_type="money"),
    _f("total_funding", COMPANY, "Total capital raised to date",
       ("funding", "total_funding", "total_raised", "funding_total",
        "total_funding_amount"), value_type="money"),
    _f("latest_funding_stage", COMPANY, "Type of the most recent funding round",
       ("latest_funding", "last_funding_type", "funding_stage", "last_round",
        "latest_funding_round", "funding_round")),
    _f("latest_funding_amount", COMPANY, "Amount raised in the most recent round",
       ("latest_funding_amount", "last_funding_amount", "last_round_amount"),
       value_type="money"),
    _f("last_funding_date", COMPANY, "When the most recent funding round closed",
       ("last_raised_at", "last_funding_date", "latest_funding_date", "last_raised"),
       value_type="date"),
    _f("retail_location_count", COMPANY,
       "Number of retail premises the company operates",
       ("number_of_retail_locations", "retail_locations", "num_locations",
        "store_count", "locations_count"), detector="integer", value_type="integer"),
    _f("technologies", COMPANY, "Technology stack the source detected on the site",
       ("technologies", "tech_stack", "installed_technologies")),
    _f("keywords", COMPANY, "Descriptive keywords the source assigns to the company",
       ("keywords", "company_keywords"), ambiguous=("tags",)),
    _f("seo_description", COMPANY, "Meta description published on the company's site",
       ("seo_description", "meta_description", "site_description")),
    # Social presences are distinct fields, not competing answers to `website`.
    # Lead-generation exports ship all four, and without their own fields three
    # of them lose the collision and their values are attributed to nothing.
    _f("facebook_url", COMPANY, "Facebook page URL",
       ("facebook", "facebook_page", "fb_url"), detector="url", value_type="url"),
    _f("instagram_url", COMPANY, "Instagram profile URL",
       ("instagram", "instagram_url", "ig_url"), detector="url", value_type="url"),
    _f("twitter_url", COMPANY, "Twitter/X profile URL",
       ("twitter", "twitter_url", "x_url"), detector="url", value_type="url"),
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


def field_by_name(
    entity_type: str, name: str, subject: str = SELF
) -> CanonicalField | None:
    """Look a field up by name *and* subject.

    Name alone stopped identifying a field once a person row could describe two
    subjects: `city` names both the person's city and their employer's, and
    they normalize differently only because one is a place and the other is a
    place belonging to a company entity.
    """
    for spec in fields_for(entity_type):
        if spec.name == name and spec.subject == subject:
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
