from validation.rules import FAIL, PASS, SEVERITY_ERROR, SEVERITY_WARNING, judge_attribute

EMP = ("employee_count", "integer", "company")
YEAR = ("founded_year", "integer", "company")


def test_range_input_is_flagged_because_normalization_distorts_it(ctx, outcome_of):
    """'50-100' normalizes to '50100' — clean-looking and completely wrong."""
    judgements = judge_attribute("50100", ctx(*EMP, raw="50-100"))
    assert outcome_of(judgements, "integer.input") == FAIL


def test_plain_integer_input_passes(ctx):
    assert all(j.outcome == PASS for j in judge_attribute("1200", ctx(*EMP, raw="1200")))


def test_impossible_headcount_is_an_error_and_zero_is_only_a_warning(ctx, judgement_for):
    huge = judgement_for(
        judge_attribute("9000000", ctx(*EMP, raw="9000000")), "employee_count.range")
    assert (huge.outcome, huge.severity) == (FAIL, SEVERITY_ERROR)

    zero = judgement_for(judge_attribute("0", ctx(*EMP, raw="0")), "employee_count.range")
    assert (zero.outcome, zero.severity) == (FAIL, SEVERITY_WARNING)


def test_founded_year_bounds(ctx, outcome_of):
    assert outcome_of(judge_attribute("1954", ctx(*YEAR, raw="1954")), "founded_year.range") == PASS
    assert outcome_of(judge_attribute("2099", ctx(*YEAR, raw="2099")), "founded_year.range") == FAIL
    assert outcome_of(judge_attribute("1400", ctx(*YEAR, raw="1400")), "founded_year.range") == FAIL


def test_a_range_rule_does_not_apply_to_the_other_field(ctx, outcome_of):
    judgements = judge_attribute("1954", ctx(*YEAR, raw="1954"))
    assert outcome_of(judgements, "employee_count.range") is None
