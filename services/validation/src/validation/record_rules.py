"""Record-level validation rules.

These judge the row as a whole rather than any single value. The question they
answer is not "is this field well-formed" but "could this record ever be
resolved to a real-world entity?" — which is precisely what the next stages
need to know, and which no attribute rule can see on its own.

Failing here does NOT mean the record is discarded. It means the record is not
safe to resolve yet; quarantine routes it for review with everything intact.
"""

from dataclasses import dataclass

from validation.rules import (
    FAIL,
    PASS,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    Judgement,
)

# Attributes that can, on their own, stand for a real-world entity. A phone
# number is deliberately absent from both: it identifies a line, not a person or
# a company, and two people sharing a switchboard would collapse into one.
IDENTIFYING_FIELDS = {
    "person": ("email", "linkedin_url", "person_external_id", "full_name"),
    "company": ("company_name", "legal_name", "website", "company_external_id", "email"),
}

# Name parts that together stand in for a full name.
NAME_PART_FIELDS = ("first_name", "last_name")

MIN_MAPPING_COVERAGE = 0.5


@dataclass
class RecordFacts:
    """What the attribute observations of one record add up to."""
    entity_type: str
    present_fields: set[str]      # canonical fields with a usable value
    total_attributes: int
    canonical_attributes: int     # attributes with a confirmed canonical field
    populated_attributes: int     # attributes the source actually filled in


def summarize(observations: list[dict], entity_type: str) -> RecordFacts:
    present, canonical, populated = set(), 0, 0
    for obs in observations:
        has_value = obs["normalized_value"] is not None
        if has_value:
            populated += 1
        if obs["canonical_field"] is not None:
            canonical += 1
            if has_value:
                present.add(obs["canonical_field"])
    return RecordFacts(entity_type, present, len(observations), canonical, populated)


def rule_all_values_missing(facts: RecordFacts) -> Judgement:
    if facts.populated_attributes > 0:
        return Judgement("record.all_values_missing", SEVERITY_ERROR, PASS)
    return Judgement(
        "record.all_values_missing", SEVERITY_ERROR, FAIL,
        "every column of this row is blank or a null placeholder",
        {"total_attributes": facts.total_attributes},
    )


def rule_has_identifier(facts: RecordFacts) -> Judgement:
    """Without an identifying attribute, entity resolution has nothing to match on.

    This is the rule that most often decides quarantine, so it names exactly
    what was missing rather than just failing.
    """
    candidates = IDENTIFYING_FIELDS[facts.entity_type]
    found = [f for f in candidates if f in facts.present_fields]

    # A first and last name together are as good as a full name.
    if (not found and facts.entity_type == "person"
            and all(part in facts.present_fields for part in NAME_PART_FIELDS)):
        return Judgement(
            "record.has_identifier", SEVERITY_ERROR, PASS, None,
            {"identified_by": list(NAME_PART_FIELDS)},
        )

    if found:
        return Judgement("record.has_identifier", SEVERITY_ERROR, PASS, None,
                         {"identified_by": found})
    return Judgement(
        "record.has_identifier", SEVERITY_ERROR, FAIL,
        "no identifying attribute, so this row cannot be resolved to an entity",
        {"required_any_of": list(candidates),
         "present_fields": sorted(facts.present_fields)},
    )


def rule_mapping_coverage(facts: RecordFacts) -> Judgement:
    """Most columns unmapped means the row is captured but barely understood."""
    if facts.total_attributes == 0:
        return Judgement("record.mapping_coverage", SEVERITY_WARNING, FAIL,
                         "the row produced no attributes at all", {})
    coverage = facts.canonical_attributes / facts.total_attributes
    details = {
        "canonical_attributes": facts.canonical_attributes,
        "total_attributes": facts.total_attributes,
        "coverage": round(coverage, 3),
    }
    if coverage >= MIN_MAPPING_COVERAGE:
        return Judgement("record.mapping_coverage", SEVERITY_WARNING, PASS, None, details)
    return Judgement(
        "record.mapping_coverage", SEVERITY_WARNING, FAIL,
        f"only {facts.canonical_attributes} of {facts.total_attributes} columns have a "
        "confirmed canonical field; review the schema mapping",
        details,
    )


RECORD_RULES = (rule_all_values_missing, rule_has_identifier, rule_mapping_coverage)


def judge_record(observations: list[dict], entity_type: str) -> list[Judgement]:
    facts = summarize(observations, entity_type)
    return [rule(facts) for rule in RECORD_RULES]
