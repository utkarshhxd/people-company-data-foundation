import pytest
from validation.rules import (
    FAIL,
    PASS,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    AttributeContext,
    judge_attribute,
)


def ctx(canonical_field, value_type, entity_type="person", raw=None, method=None):
    return AttributeContext(
        canonical_field=canonical_field,
        entity_type=entity_type,
        value_type=value_type,
        raw_value=raw if raw is not None else "",
        normalization_method=method or value_type,
    )


def outcome_of(judgements, rule_id):
    for judgement in judgements:
        if judgement.rule_id == rule_id:
            return judgement.outcome
    return None


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("taf@pk.asiafound.org", PASS),
        ("webmaster@nationalmortgagenews.com", PASS),
        ("first.last+tag@sub.example.co.uk", PASS),
        ("no-at-sign.example.com", FAIL),
        ("missing@domain", FAIL),          # no TLD
        ("two@@example.com", FAIL),
        ("spaced address@example.com", FAIL),
    ],
)
def test_email_syntax(value, expected):
    assert outcome_of(judge_attribute(value, ctx("email", "email")), "email.syntax") == expected


def test_role_mailbox_is_a_warning_for_a_person_not_an_error():
    """A role address is real data — it just may not identify the person."""
    judgements = judge_attribute("info@example.com", ctx("email", "email", "person"))
    role = next(j for j in judgements if j.rule_id == "email.role_account")
    assert role.outcome == FAIL
    assert role.severity == SEVERITY_WARNING
    assert outcome_of(judgements, "email.syntax") == PASS


def test_role_mailbox_does_not_apply_to_a_company():
    """info@ is exactly what a company address should be, so the rule stays silent."""
    judgements = judge_attribute("info@example.com", ctx("email", "email", "company"))
    assert outcome_of(judgements, "email.role_account") is None


# --------------------------------------------------------------------------
# phone
# --------------------------------------------------------------------------

def test_phone_digit_count_bounds():
    assert outcome_of(
        judge_attribute("+919876543210", ctx("phone", "phone")), "phone.digit_count"
    ) == PASS
    assert outcome_of(
        judge_attribute("12345", ctx("phone", "phone")), "phone.digit_count"
    ) == FAIL


def test_missing_country_code_is_a_warning_never_an_error():
    """Normalization refuses to guess a country; validation records the ambiguity."""
    judgements = judge_attribute("18003514494", ctx("phone", "phone"))
    country = next(j for j in judgements if j.rule_id == "phone.country_code")
    assert country.outcome == FAIL
    assert country.severity == SEVERITY_WARNING
    # The number itself is perfectly dialable — it must not be called invalid.
    assert outcome_of(judgements, "phone.digit_count") == PASS


# --------------------------------------------------------------------------
# url
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("www.nationalmortgagenews.com", PASS),
        ("https://www.asiafoundation.org", PASS),
        ("crazeegirl.com", PASS),
        ("localhost", FAIL),
        ("not a url", FAIL),
    ],
)
def test_url_host(value, expected):
    assert outcome_of(judge_attribute(value, ctx("website", "url")), "url.host") == expected


def test_linkedin_url_must_actually_be_linkedin():
    judgements = judge_attribute(
        "https://twitter.com/someone", ctx("linkedin_url", "url")
    )
    assert outcome_of(judgements, "linkedin.host") == FAIL
    # The URL is well-formed; only the host is wrong for this field.
    assert outcome_of(judgements, "url.host") == PASS


def test_linkedin_rule_does_not_apply_to_other_url_fields():
    judgements = judge_attribute("https://example.com", ctx("website", "url"))
    assert outcome_of(judgements, "linkedin.host") is None


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------

def test_single_character_name_is_an_error():
    assert outcome_of(
        judge_attribute("A", ctx("full_name", "person_name")), "name.length"
    ) == FAIL


def test_placeholder_name_is_flagged_but_not_rejected():
    judgements = judge_attribute("Test", ctx("full_name", "person_name"))
    placeholder = next(j for j in judgements if j.rule_id == "name.placeholder")
    assert placeholder.outcome == FAIL
    assert placeholder.severity == SEVERITY_WARNING


def test_name_holding_an_email_is_flagged_as_drifted():
    assert outcome_of(
        judge_attribute("a@b.com", ctx("full_name", "person_name")), "name.shape"
    ) == FAIL


def test_ordinary_name_passes_every_name_rule():
    judgements = judge_attribute("Aruna Rodrigues", ctx("full_name", "person_name"))
    assert all(j.outcome == PASS for j in judgements)


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------

def test_range_input_is_flagged_because_normalization_distorts_it():
    """'50-100' normalizes to '50100' — clean-looking and completely wrong."""
    judgements = judge_attribute(
        "50100", ctx("employee_count", "integer", "company", raw="50-100")
    )
    assert outcome_of(judgements, "integer.input") == FAIL


def test_plain_integer_input_passes():
    judgements = judge_attribute(
        "1200", ctx("employee_count", "integer", "company", raw="1200")
    )
    assert all(j.outcome == PASS for j in judgements)


def test_impossible_headcount_is_an_error_and_zero_is_only_a_warning():
    huge = judge_attribute(
        "9000000", ctx("employee_count", "integer", "company", raw="9000000")
    )
    rule = next(j for j in huge if j.rule_id == "employee_count.range")
    assert (rule.outcome, rule.severity) == (FAIL, SEVERITY_ERROR)

    zero = judge_attribute("0", ctx("employee_count", "integer", "company", raw="0"))
    rule = next(j for j in zero if j.rule_id == "employee_count.range")
    assert (rule.outcome, rule.severity) == (FAIL, SEVERITY_WARNING)


def test_founded_year_bounds():
    assert outcome_of(
        judge_attribute("1954", ctx("founded_year", "integer", "company", raw="1954")),
        "founded_year.range",
    ) == PASS
    assert outcome_of(
        judge_attribute("2099", ctx("founded_year", "integer", "company", raw="2099")),
        "founded_year.range",
    ) == FAIL
    assert outcome_of(
        judge_attribute("1400", ctx("founded_year", "integer", "company", raw="1400")),
        "founded_year.range",
    ) == FAIL


# --------------------------------------------------------------------------
# values that carry nothing
# --------------------------------------------------------------------------

def test_a_null_placeholder_produces_no_judgement_at_all():
    """The source said nothing. That is not a failure, and not a pass either."""
    assert judge_attribute(None, ctx("phone", "phone", method="phone:null_token")) == []


def test_a_value_that_normalizes_to_nothing_is_an_error():
    """'abc' in a phone column leaves zero digits — the source wrote something unusable."""
    judgements = judge_attribute(None, ctx("phone", "phone", raw="abc", method="phone:empty"))
    assert len(judgements) == 1
    assert judgements[0].rule_id == "value.empty_after_normalization"
    assert judgements[0].severity == SEVERITY_ERROR


def test_normalization_failure_is_surfaced_not_swallowed():
    judgements = judge_attribute(None, ctx("phone", "phone", raw="?", method="phone:failed"))
    assert judgements[0].rule_id == "value.normalization_failed"


# --------------------------------------------------------------------------
# restraint
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "canonical_field,value_type,value",
    [
        ("address_line1", "address", "1722;19th Street;NW;#610 - Washington;DC"),
        ("address_line1", "address", "1720WisconsinAveNW"),
        ("company_external_id", "identifier", "00501"),
        ("city", "place_name", "Washington"),
        ("state_region", "region_code", "DC"),
        ("sic_description", "text", "Foreign trade & international banking"),
    ],
)
def test_types_without_rules_produce_no_judgements(canonical_field, value_type, value):
    """Any string can be a street address, a vendor's key, or a town name.

    Inventing rules for these would manufacture failures rather than find them.
    """
    assert judge_attribute(value, ctx(canonical_field, value_type, "company")) == []


def test_a_raising_rule_is_reported_and_never_mistaken_for_a_pass():
    from validation import rules

    def broken(value, context):
        raise RuntimeError("boom")

    original = rules.RULES_BY_VALUE_TYPE["email"]
    rules.RULES_BY_VALUE_TYPE["email"] = (broken,)
    try:
        judgements = judge_attribute("a@b.com", ctx("email", "email"))
    finally:
        rules.RULES_BY_VALUE_TYPE["email"] = original

    assert len(judgements) == 1
    assert judgements[0].rule_id == "rule.error"
    assert judgements[0].outcome == FAIL
    assert judgements[0].severity == SEVERITY_INFO
