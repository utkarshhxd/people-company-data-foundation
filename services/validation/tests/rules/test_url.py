import pytest
from validation.rules import FAIL, PASS, judge_attribute


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
def test_host(value, expected, ctx, outcome_of):
    assert outcome_of(judge_attribute(value, ctx("website", "url")), "url.host") == expected


def test_linkedin_url_must_actually_be_linkedin(ctx, outcome_of):
    judgements = judge_attribute("https://twitter.com/someone", ctx("linkedin_url", "url"))
    assert outcome_of(judgements, "linkedin.host") == FAIL
    # The URL is well-formed; only the host is wrong for this field.
    assert outcome_of(judgements, "url.host") == PASS


def test_linkedin_rule_does_not_apply_to_other_url_fields(ctx, outcome_of):
    judgements = judge_attribute("https://example.com", ctx("website", "url"))
    assert outcome_of(judgements, "linkedin.host") is None


def test_a_url_carrying_credentials_is_flagged(ctx, outcome_of):
    """A password in a vendor file is a secret, not a website."""
    judgements = judge_attribute("https://user:pw@example.com/x", ctx("website", "url"))
    assert outcome_of(judgements, "url.embedded_credentials") == FAIL
