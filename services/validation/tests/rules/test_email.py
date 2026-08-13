import pytest
from validation.rules import FAIL, PASS, SEVERITY_WARNING, judge_attribute


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
def test_syntax(value, expected, ctx, outcome_of):
    assert outcome_of(judge_attribute(value, ctx("email", "email")), "email.syntax") == expected


def test_an_over_long_address_is_undeliverable(ctx, outcome_of):
    value = "a" * 70 + "@example.com"
    assert outcome_of(judge_attribute(value, ctx("email", "email")), "email.length") == FAIL


def test_role_mailbox_is_a_warning_for_a_person_not_an_error(ctx, judgement_for, outcome_of):
    """A role address is real data — it just may not identify the person."""
    judgements = judge_attribute("info@example.com", ctx("email", "email", "person"))
    role = judgement_for(judgements, "email.role_account")
    assert role.outcome == FAIL
    assert role.severity == SEVERITY_WARNING
    assert outcome_of(judgements, "email.syntax") == PASS


def test_role_mailbox_does_not_apply_to_a_company(ctx, outcome_of):
    """info@ is exactly what a company address should be, so the rule stays silent."""
    judgements = judge_attribute("info@example.com", ctx("email", "email", "company"))
    assert outcome_of(judgements, "email.role_account") is None


def test_a_consumer_mailbox_on_a_company_is_flagged(ctx, outcome_of):
    """A gmail address is the contact's, not the organisation's."""
    judgements = judge_attribute("someone@gmail.com", ctx("email", "email", "company"))
    assert outcome_of(judgements, "email.consumer_domain") == FAIL


def test_a_consumer_mailbox_on_a_person_is_perfectly_normal(ctx, outcome_of):
    judgements = judge_attribute("someone@gmail.com", ctx("email", "email", "person"))
    assert outcome_of(judgements, "email.consumer_domain") is None
