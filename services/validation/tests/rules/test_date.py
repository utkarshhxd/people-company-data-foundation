"""Date rules. Format is the normalizer's job; only plausibility is judged here."""

from datetime import UTC, datetime, timedelta

from validation.rules import FAIL, PASS, SEVERITY_ERROR, SEVERITY_WARNING, judge_attribute


def _iso(days_from_today: int) -> str:
    return (datetime.now(UTC).date() + timedelta(days=days_from_today)).isoformat()


def test_a_past_date_passes(ctx, outcome_of):
    judgements = judge_attribute(
        "2024-09-01", ctx("last_funding_date", "date", "company", raw="2024-09-01"))
    assert outcome_of(judgements, "date.future") == PASS
    assert outcome_of(judgements, "date.range") == PASS


def test_a_date_far_in_the_future_is_an_error(ctx, judgement_for):
    judgements = judge_attribute(
        _iso(400), ctx("last_funding_date", "date", "company", raw=_iso(400)))
    judgement = judgement_for(judgements, "date.future")
    assert judgement.outcome == FAIL
    assert judgement.severity == SEVERITY_ERROR


def test_tomorrow_is_tolerated(ctx, outcome_of):
    """Vendors export in local time and we compare in UTC. A genuinely current
    date can read as tomorrow without anything being wrong."""
    judgements = judge_attribute(
        _iso(1), ctx("last_funding_date", "date", "company", raw=_iso(1)))
    assert outcome_of(judgements, "date.future") == PASS


def test_an_epoch_default_is_reported(ctx, judgement_for):
    """1970-01-01 in a funding column is almost always an unset timestamp."""
    judgements = judge_attribute(
        "1899-12-31", ctx("last_funding_date", "date", "company", raw="1899-12-31"))
    judgement = judgement_for(judgements, "date.range")
    assert judgement.outcome == FAIL
    assert judgement.severity == SEVERITY_WARNING


def test_todays_date_passes(ctx, outcome_of):
    judgements = judge_attribute(
        _iso(0), ctx("last_funding_date", "date", "company", raw=_iso(0)))
    assert outcome_of(judgements, "date.future") == PASS


def test_an_iso_date_is_not_flagged_as_inferred(ctx, outcome_of):
    judgements = judge_attribute(
        "2024-09-01", ctx("last_funding_date", "date", "company",
                          raw="2024-09-01T00:00:00+00:00"))
    assert outcome_of(judgements, "date.input") == PASS


def test_a_decoded_excel_serial_is_reported(ctx, judgement_for):
    """The decode is right, but it is our arithmetic on an integer rather than a
    date the vendor wrote, and that difference has to stay visible."""
    judgements = judge_attribute(
        "2023-05-01", ctx("last_funding_date", "date", "company", raw="45047"))
    judgement = judgement_for(judgements, "date.input")
    assert judgement.outcome == FAIL
    assert judgement.severity == SEVERITY_WARNING
    assert judgement.details["raw_value"] == "45047"
