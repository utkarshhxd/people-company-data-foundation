from validation.rules import FAIL, PASS, SEVERITY_WARNING, judge_attribute

POSTAL = ("postal_code", "postal_code", "company")


def test_a_normal_code_passes(ctx, outcome_of):
    assert outcome_of(judge_attribute("20036-2109", ctx(*POSTAL)), "postal_code.shape") == PASS


def test_an_absurdly_long_value_is_flagged(ctx, outcome_of):
    assert outcome_of(judge_attribute("1234567890123", ctx(*POSTAL)), "postal_code.shape") == FAIL


def test_a_lost_leading_zero_is_caught(ctx, judgement_for):
    """00501 arriving as 501 means a spreadsheet ate the zero before we saw it."""
    lost = judgement_for(judge_attribute("501", ctx(*POSTAL)), "postal_code.us_zip_truncation")
    assert lost.outcome == FAIL
    assert lost.severity == SEVERITY_WARNING


def test_a_preserved_leading_zero_is_fine(ctx, outcome_of):
    judgements = judge_attribute("00501", ctx(*POSTAL))
    assert outcome_of(judgements, "postal_code.us_zip_truncation") == PASS


def test_a_short_non_numeric_code_is_not_treated_as_a_lost_zero(ctx, outcome_of):
    """UK and Canadian codes are short and alphanumeric — not truncated US ZIPs."""
    judgements = judge_attribute("EC1A", ctx(*POSTAL))
    assert outcome_of(judgements, "postal_code.us_zip_truncation") == PASS


def test_every_postal_rule_is_a_warning(ctx):
    """We do not reliably know the country, so we may not reject on format."""
    judgements = judge_attribute("!!!", ctx(*POSTAL))
    assert all(j.severity == SEVERITY_WARNING for j in judgements)
