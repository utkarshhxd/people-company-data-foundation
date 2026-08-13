"""Which fields a record is expected to carry, and how hard that expectation is.

Three tiers, because "required" means different things depending on what breaks
without the field:

  identifying  - without at least one of these the record can never be resolved
                 to a real-world entity. This is the only ERROR tier, and it is
                 enforced as a set rather than per-field: an email alone is
                 enough, and so is a name, so demanding both would reject
                 perfectly usable records.

  core         - the fields that make a record worth having. A company with no
                 name and no website is technically resolvable via its vendor id
                 but useless to anyone reading it. WARNING.

  expected     - commonly present and useful when absent is worth knowing, but
                 routinely missing in legitimate data. INFO, so it is countable
                 without ever affecting a record's status.

Deliberately NOT a per-field "required" flag. Source files vary enormously in
which columns they carry, and a hard per-field requirement would quarantine most
of a real vendor export over a missing postcode — which would mean the pipeline
rejecting data it is perfectly capable of using.
"""

# At least one of these must be present, or entity resolution has nothing to
# match on. A phone number is absent from both sets on purpose: it identifies a
# line, not a person or a company.
IDENTIFYING_FIELDS: dict[str, tuple[str, ...]] = {
    "person": ("email", "linkedin_url", "person_external_id", "full_name"),
    "company": ("company_name", "legal_name", "website", "company_external_id", "email"),
}

# Name parts that together stand in for a full name.
NAME_PART_FIELDS = ("first_name", "last_name")

CORE_FIELDS: dict[str, tuple[str, ...]] = {
    "person": ("full_name", "email"),
    "company": ("company_name",),
}

EXPECTED_FIELDS: dict[str, tuple[str, ...]] = {
    "person": ("phone", "job_title", "company_name", "city", "country"),
    "company": ("website", "phone", "address_line1", "city", "country", "industry"),
}


def identifying_fields(entity_type: str) -> tuple[str, ...]:
    return IDENTIFYING_FIELDS[entity_type]


def missing_core(entity_type: str, present: set[str]) -> list[str]:
    return [f for f in CORE_FIELDS.get(entity_type, ()) if f not in present]


def missing_expected(entity_type: str, present: set[str]) -> list[str]:
    return [f for f in EXPECTED_FIELDS.get(entity_type, ()) if f not in present]
