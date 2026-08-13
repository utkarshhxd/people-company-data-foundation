from validation.rules import FAIL, PASS, SEVERITY_WARNING, judge_attribute

NAME = ("full_name", "person_name")


def test_single_character_name_is_an_error(ctx, outcome_of):
    assert outcome_of(judge_attribute("A", ctx(*NAME)), "name.length") == FAIL


def test_placeholder_name_is_flagged_but_not_rejected(ctx, judgement_for):
    placeholder = judgement_for(judge_attribute("Test", ctx(*NAME)), "name.placeholder")
    assert placeholder.outcome == FAIL
    assert placeholder.severity == SEVERITY_WARNING


def test_a_bare_honorific_names_no_one(ctx, outcome_of):
    assert outcome_of(judge_attribute("Dr.", ctx(*NAME)), "name.placeholder") == FAIL


def test_name_holding_an_email_is_flagged_as_drifted(ctx, outcome_of):
    assert outcome_of(judge_attribute("a@b.com", ctx(*NAME)), "name.shape") == FAIL


def test_a_single_token_full_name_is_only_a_warning(ctx, judgement_for):
    """Mononyms are real and common; this is a prompt to look, not a verdict."""
    partial = judgement_for(judge_attribute("Madonna", ctx(*NAME)), "full_name.completeness")
    assert partial.outcome == FAIL
    assert partial.severity == SEVERITY_WARNING


def test_completeness_does_not_apply_to_a_first_name_column(ctx, outcome_of):
    judgements = judge_attribute("Aruna", ctx("first_name", "person_name"))
    assert outcome_of(judgements, "full_name.completeness") is None


def test_ordinary_name_passes_every_name_rule(ctx):
    judgements = judge_attribute("Aruna Rodrigues", ctx(*NAME))
    assert all(j.outcome == PASS for j in judgements)
