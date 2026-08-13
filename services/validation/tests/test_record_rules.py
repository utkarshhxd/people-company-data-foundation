from validation.record_rules import judge_record
from validation.rules import FAIL, PASS, SEVERITY_ERROR


def obs(canonical_field, normalized_value):
    return {"canonical_field": canonical_field, "normalized_value": normalized_value}


def outcome_of(judgements, rule_id):
    return next(j.outcome for j in judgements if j.rule_id == rule_id)


def judgement_for(judgements, rule_id):
    return next(j for j in judgements if j.rule_id == rule_id)


def test_email_alone_identifies_a_person():
    judgements = judge_record([obs("email", "a@b.com")], "person")
    assert outcome_of(judgements, "record.has_identifier") == PASS


def test_first_and_last_name_together_identify_a_person():
    judgements = judge_record(
        [obs("first_name", "Aruna"), obs("last_name", "Rodrigues")], "person"
    )
    identifier = judgement_for(judgements, "record.has_identifier")
    assert identifier.outcome == PASS
    assert identifier.details["identified_by"] == ["first_name", "last_name"]


def test_a_phone_number_alone_does_not_identify_anyone():
    """A phone identifies a line, not a person — two colleagues share a switchboard."""
    judgements = judge_record([obs("phone", "+14153928863")], "person")
    identifier = judgement_for(judgements, "record.has_identifier")
    assert identifier.outcome == FAIL
    assert identifier.severity == SEVERITY_ERROR
    assert "phone" not in identifier.details["required_any_of"]


def test_company_name_identifies_a_company():
    judgements = judge_record([obs("company_name", "The Asia Foundation")], "company")
    assert outcome_of(judgements, "record.has_identifier") == PASS


def test_website_alone_identifies_a_company():
    judgements = judge_record([obs("website", "asiafoundation.org")], "company")
    assert outcome_of(judgements, "record.has_identifier") == PASS


def test_a_mapped_field_with_no_value_does_not_count_as_an_identifier():
    """The column exists; the source left it blank. That identifies nothing."""
    judgements = judge_record([obs("email", None), obs("phone", "+14153928863")], "person")
    assert outcome_of(judgements, "record.has_identifier") == FAIL


def test_an_entirely_blank_row_fails_both_error_rules():
    judgements = judge_record([obs("email", None), obs(None, None)], "person")
    assert outcome_of(judgements, "record.all_values_missing") == FAIL
    assert outcome_of(judgements, "record.has_identifier") == FAIL


def test_a_row_with_any_value_passes_the_blank_check():
    """Even a value in an unmapped column proves the source wrote something."""
    judgements = judge_record([obs(None, "some vendor code")], "person")
    assert outcome_of(judgements, "record.all_values_missing") == PASS


def test_missing_core_fields_warn_that_the_record_is_illegible():
    """Resolvable by vendor id, but nobody could read it and recognise anything."""
    judgements = judge_record([obs("company_external_id", "501")], "company")
    core = judgement_for(judgements, "record.core_fields")
    assert core.outcome == FAIL
    assert core.details["missing"] == ["company_name"]
    # Still resolvable, so this must not be an error.
    assert core.severity == "warning"
    assert outcome_of(judgements, "record.has_identifier") == PASS


def test_a_complete_record_passes_the_core_check():
    judgements = judge_record(
        [obs("company_name", "Acme"), obs("website", "acme.com")], "company")
    assert outcome_of(judgements, "record.core_fields") == PASS


def test_absent_expected_fields_are_recorded_but_never_judged():
    """Coverage reporting, not a verdict — plenty of good records lack these."""
    judgements = judge_record([obs("company_name", "Acme")], "company")
    expected = judgement_for(judgements, "record.expected_fields")
    assert expected.outcome == FAIL
    assert expected.severity == "info"
    assert "website" in expected.details["missing"]


def test_mostly_unmapped_columns_warn_that_the_row_is_barely_understood():
    observations = [obs("email", "a@b.com")] + [obs(None, "x") for _ in range(9)]
    coverage = judgement_for(judge_record(observations, "person"), "record.mapping_coverage")
    assert coverage.outcome == FAIL
    assert coverage.details["coverage"] == 0.1
    # The row is still identifiable — poor coverage is a warning, not invalidity.
    assert coverage.severity == "warning"


def test_good_coverage_passes():
    observations = [obs("email", "a@b.com"), obs("full_name", "A B"), obs(None, "x")]
    assert outcome_of(judge_record(observations, "person"), "record.mapping_coverage") == PASS
