"""Money rules. Magnitude only — nothing here can know what a company earns."""

import pytest
from validation.rules import FAIL, PASS, SEVERITY_ERROR, SEVERITY_WARNING, judge_attribute
from validation.rules.money import MAX_AMOUNT


def test_a_plain_stated_figure_passes_everything(ctx, outcome_of):
    judgements = judge_attribute(
        "3181850000", ctx("total_funding", "money", "company", raw="3181850000"))
    assert outcome_of(judgements, "money.input") == PASS
    assert outcome_of(judgements, "money.range") == PASS


def test_thousands_separators_are_not_treated_as_inference(ctx, outcome_of):
    """A comma is punctuation, not a magnitude claim. '3,181,850,000' is still
    the vendor stating every digit."""
    judgements = judge_attribute(
        "3181850000", ctx("total_funding", "money", "company", raw="3,181,850,000"))
    assert outcome_of(judgements, "money.input") == PASS


def test_a_magnitude_suffix_is_reported_as_inferred(ctx, judgement_for):
    """'$1.2M' became 1200000 by multiplication. Almost certainly right, but it
    is our arithmetic and not the vendor's figure, so it must be visible."""
    judgements = judge_attribute(
        "1200000", ctx("annual_revenue", "money", "company", raw="$1.2M"))
    judgement = judgement_for(judgements, "money.input")
    assert judgement.outcome == FAIL
    assert judgement.severity == SEVERITY_WARNING
    assert judgement.details["raw_value"] == "$1.2M"


def test_an_amount_beyond_any_company_is_an_error(ctx, judgement_for):
    """A figure this large means the column held something that is not money."""
    judgements = judge_attribute(
        str(MAX_AMOUNT + 1),
        ctx("annual_revenue", "money", "company", raw=str(MAX_AMOUNT + 1)))
    judgement = judgement_for(judgements, "money.range")
    assert judgement.outcome == FAIL
    assert judgement.severity == SEVERITY_ERROR


def test_the_largest_real_revenue_on_earth_still_passes(ctx, outcome_of):
    """The ceiling exists to catch the wrong kind of number, not to adjudicate
    between large companies. Walmart-scale revenue must never trip it."""
    judgements = judge_attribute(
        "680000000000", ctx("annual_revenue", "money", "company", raw="680000000000"))
    assert outcome_of(judgements, "money.range") == PASS


def test_a_negative_amount_is_an_error(ctx, judgement_for):
    judgements = judge_attribute(
        "-500", ctx("total_funding", "money", "company", raw="-500"))
    judgement = judgement_for(judgements, "money.range")
    assert judgement.outcome == FAIL
    assert judgement.severity == SEVERITY_ERROR


def test_zero_is_only_a_warning(ctx, judgement_for):
    """Zero funding is a real and common answer for a company that never raised."""
    judgements = judge_attribute(
        "0", ctx("total_funding", "money", "company", raw="0"))
    judgement = judgement_for(judgements, "money.range")
    assert judgement.outcome == FAIL
    assert judgement.severity == SEVERITY_WARNING


@pytest.mark.parametrize("field", ["annual_revenue", "total_funding",
                                   "latest_funding_amount"])
def test_the_rules_apply_to_every_money_field(field, ctx, outcome_of):
    """Dispatch is by value type, so no money field can be accidentally exempt."""
    judgements = judge_attribute("1000000", ctx(field, "money", "company", raw="1000000"))
    assert outcome_of(judgements, "money.range") == PASS
