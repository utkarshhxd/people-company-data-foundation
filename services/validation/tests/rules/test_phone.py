from validation.rules import FAIL, PASS, SEVERITY_WARNING, judge_attribute


def test_digit_count_bounds(ctx, outcome_of):
    assert outcome_of(
        judge_attribute("+919876543210", ctx("phone", "phone")), "phone.digit_count"
    ) == PASS
    assert outcome_of(
        judge_attribute("12345", ctx("phone", "phone")), "phone.digit_count"
    ) == FAIL


def test_missing_country_code_is_a_warning_never_an_error(ctx, judgement_for, outcome_of):
    """Normalization refuses to guess a country; validation records the ambiguity."""
    judgements = judge_attribute("18003514494", ctx("phone", "phone"))
    country = judgement_for(judgements, "phone.country_code")
    assert country.outcome == FAIL
    assert country.severity == SEVERITY_WARNING
    # The number itself is perfectly dialable — it must not be called invalid.
    assert outcome_of(judgements, "phone.digit_count") == PASS


def test_a_repeated_digit_is_filler_not_a_number(ctx, outcome_of):
    assert outcome_of(
        judge_attribute("0000000000", ctx("phone", "phone")), "phone.filler"
    ) == FAIL


def test_a_digit_sequence_is_filler(ctx, outcome_of):
    assert outcome_of(
        judge_attribute("1234567890", ctx("phone", "phone")), "phone.filler"
    ) == FAIL


def test_a_real_number_is_not_filler(ctx, outcome_of):
    assert outcome_of(
        judge_attribute("+14153928863", ctx("phone", "phone")), "phone.filler"
    ) == PASS


def test_an_extension_merged_into_the_number_is_flagged(ctx, outcome_of):
    """Normalization keeps digits only, so 'x204' becomes part of the number.

    Only the raw value shows the damage — afterwards it looks like a valid,
    slightly longer number.
    """
    judgements = judge_attribute(
        "12025889420204", ctx("phone", "phone", raw="+1 202 588 9420 x204")
    )
    assert outcome_of(judgements, "phone.extension_merged") == FAIL


def test_an_ordinary_number_has_no_extension_warning(ctx, outcome_of):
    judgements = judge_attribute("+12025889420", ctx("phone", "phone", raw="+1 202 588 9420"))
    assert outcome_of(judgements, "phone.extension_merged") == PASS
