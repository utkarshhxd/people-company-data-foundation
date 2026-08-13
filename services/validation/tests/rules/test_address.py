"""Address rules.

The governing constraint: these check *plausibility*, never *format*. Nothing
here may reject an address for being foreign, unpunctuated, or shaped unlike a
US street address, and nothing here is error-severity. The tests below are as
much about what does NOT fire as what does.
"""

import pytest
from validation.rules import FAIL, PASS, SEVERITY_INFO, SEVERITY_WARNING, judge_attribute

ADDR = ("address_line1", "address")


@pytest.mark.parametrize(
    "value",
    [
        "1779 Massachusetts Ave NW #815",
        "1722;19th Street;NW;#610 - Washington;DC",   # semicolons are the source's
        "465 California St 9th Floor",
        "Flat 4, 21 Rue de la Paix",
        "12-3-45 Nakameguro, Meguro-ku",
        "Plot 7, Sector 62, Noida",
    ],
)
def test_real_addresses_from_anywhere_pass_every_rule(value, ctx, outcome_of):
    """No rule may punish an address for its country's conventions."""
    judgements = judge_attribute(value, ctx(*ADDR, entity_type="company"))
    failures = [j.rule_id for j in judgements if j.outcome == FAIL]
    assert failures == []


def test_no_address_rule_is_ever_an_error(ctx):
    """An unusual address is a reason to look, never a reason to quarantine."""
    judgements = judge_attribute("x", ctx(*ADDR, entity_type="company"))
    assert all(j.severity in (SEVERITY_WARNING, SEVERITY_INFO) for j in judgements)


def test_a_value_too_short_to_locate_anything_is_flagged(ctx, outcome_of):
    assert outcome_of(
        judge_attribute("NW", ctx(*ADDR, entity_type="company")), "address.length"
    ) == FAIL


@pytest.mark.parametrize("value", ["same as above", "See Above", "not provided", "TBD"])
def test_placeholder_text_is_flagged(value, ctx, outcome_of):
    """These survive normalization because they are not null tokens."""
    assert outcome_of(
        judge_attribute(value, ctx(*ADDR, entity_type="company")), "address.placeholder"
    ) == FAIL


def test_a_run_together_address_is_flagged_but_never_corrected(ctx, judgement_for):
    """'1720WisconsinAveNW' is real vendor data with the spaces lost upstream.

    Normalization passes it through untouched — inventing the spaces would
    fabricate a value the source never wrote. This records the damage instead.
    """
    judgements = judge_attribute(
        "1720WisconsinAveNW", ctx(*ADDR, entity_type="company")
    )
    spacing = judgement_for(judgements, "address.spacing")
    assert spacing.outcome == FAIL
    assert spacing.severity == SEVERITY_WARNING
    assert spacing.details["token"] == "1720WisconsinAveNW"


def test_normal_capitalisation_is_not_mistaken_for_lost_spacing(ctx, outcome_of):
    judgements = judge_attribute(
        "1779 Massachusetts Ave NW", ctx(*ADDR, entity_type="company")
    )
    assert outcome_of(judgements, "address.spacing") == PASS


@pytest.mark.parametrize(
    "value", ["someone@example.com", "https://example.com", "www.example.com"]
)
def test_another_field_drifting_into_the_address_column_is_flagged(value, ctx, outcome_of):
    assert outcome_of(
        judge_attribute(value, ctx(*ADDR, entity_type="company")), "address.drifted_field"
    ) == FAIL


def test_a_missing_street_number_is_recorded_but_not_judged(ctx, judgement_for):
    """PO boxes, rural addresses and named buildings legitimately have no number."""
    judgements = judge_attribute("Rose Cottage, Lower Slaughter", ctx(*ADDR, entity_type="company"))
    number = judgement_for(judgements, "address.street_number")
    assert number.outcome == FAIL
    assert number.severity == SEVERITY_INFO   # never affects record status
