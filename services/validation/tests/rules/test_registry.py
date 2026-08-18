"""The dispatcher itself, and the properties that must hold across all modules."""

import pytest
from validation import rules
from validation.rules import (
    FAIL,
    RULES_BY_VALUE_TYPE,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    UNRULED_VALUE_TYPES,
    judge_attribute,
)


def test_a_null_placeholder_produces_no_judgement_at_all(ctx):
    """The source said nothing. That is not a failure, and not a pass either."""
    assert judge_attribute(None, ctx("phone", "phone", method="phone:null_token")) == []


def test_a_value_that_normalizes_to_nothing_is_an_error(ctx):
    """'abc' in a phone column leaves zero digits — the source wrote something unusable."""
    judgements = judge_attribute(None, ctx("phone", "phone", raw="abc", method="phone:empty"))
    assert len(judgements) == 1
    assert judgements[0].rule_id == "value.empty_after_normalization"
    assert judgements[0].severity == SEVERITY_ERROR


def test_normalization_failure_is_surfaced_not_swallowed(ctx):
    judgements = judge_attribute(None, ctx("phone", "phone", raw="?", method="phone:failed"))
    assert judgements[0].rule_id == "value.normalization_failed"


@pytest.mark.parametrize(
    "canonical_field,value_type,value",
    [
        ("company_external_id", "identifier", "00501"),
        ("city", "place_name", "Washington"),
        ("state_region", "region_code", "DC"),
        ("sic_description", "text", "Foreign trade & international banking"),
        ("email_status", "lower_token", "verified"),
    ],
)
def test_types_without_rules_produce_no_judgements(canonical_field, value_type, value, ctx):
    """A vendor's own key, a town name and a SIC description are all legitimately
    arbitrary. Rules there would manufacture failures rather than find them."""
    assert judge_attribute(value, ctx(canonical_field, value_type, "company")) == []


def test_the_unruled_set_and_the_registry_do_not_overlap():
    """Documentation and behaviour must not be able to drift apart."""
    assert not (UNRULED_VALUE_TYPES & set(RULES_BY_VALUE_TYPE))


def test_every_registered_module_actually_supplies_rules():
    assert all(len(group) > 0 for group in RULES_BY_VALUE_TYPE.values())


def test_every_rule_id_is_namespaced(ctx):
    """A flat rule id would collide the moment two modules pick the same word."""
    samples = [
        ("email", "email", "a@b.com"),
        ("phone", "phone", "+14155550100"),
        ("website", "url", "example.com"),
        ("full_name", "person_name", "Aruna Rodrigues"),
        ("address_line1", "address", "1 High Street"),
        ("postal_code", "postal_code", "20036"),
    ]
    for field, value_type, value in samples:
        for judgement in judge_attribute(value, ctx(field, value_type, "company")):
            assert "." in judgement.rule_id, judgement.rule_id


def test_a_raising_rule_is_reported_and_never_mistaken_for_a_pass(ctx):
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


def test_every_value_type_in_the_schema_is_either_ruled_or_deliberately_unruled():
    """The gap this closes: a new canonical field brings a new value type, no
    rule module claims it, and its values are never judged at all. Silence there
    is indistinguishable from passing, so the decision has to be explicit."""
    from common.canonical import COMPANY, PERSON, fields_for

    declared = set(RULES_BY_VALUE_TYPE) | UNRULED_VALUE_TYPES
    in_use = {f.value_type for e in (PERSON, COMPANY) for f in fields_for(e)}
    assert in_use <= declared, f"undeclared value types: {sorted(in_use - declared)}"


def test_a_range_is_reported_as_a_range_not_as_an_empty_value(ctx):
    """The record keeps its other fields: this is a warning about one value, not
    a verdict on the row."""
    judgements = judge_attribute(
        None, ctx("employee_count", "integer", "company",
                  raw="50-100", method="integer:range"))
    assert len(judgements) == 1
    assert judgements[0].rule_id == "value.range_given"
    assert judgements[0].severity == "warning"
