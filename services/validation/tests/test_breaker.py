"""When validation should stop itself, and when it must not.

The second half matters more than the first. A breaker that trips on ordinary
messy data is a breaker somebody disables in week two, and then it is not there
on the night it was needed.
"""

import pytest
from common.config import settings
from validation import breaker


@pytest.fixture(autouse=True)
def _known_thresholds(monkeypatch):
    monkeypatch.setattr(settings, "validation_breaker_enabled", True)
    monkeypatch.setattr(settings, "validation_breaker_threshold", 0.95)
    monkeypatch.setattr(settings, "validation_breaker_min_records", 100)


def test_a_whole_feed_coming_back_invalid_trips_it():
    verdict = breaker.assess(records=500, invalid=500)
    assert verdict.tripped
    assert verdict.rate == 1.0


def test_a_vendor_with_messy_data_does_not_trip_it():
    """The premise of this system is that vendor exports are messy.

    Fifty invalid records out of fifty thousand is the pipeline working, and a
    breaker that fires on it would be turned off before it was ever useful.
    """
    verdict = breaker.assess(records=50_000, invalid=50)
    assert not verdict.tripped


def test_a_tiny_file_cannot_trip_it_however_bad_it_is():
    """Two rows, both invalid, is not evidence about anything."""
    verdict = breaker.assess(records=2, invalid=2)
    assert verdict.rate == 1.0
    assert not verdict.tripped, "below the floor, a rate means nothing"


def test_the_floor_is_a_floor_not_a_rounding():
    assert not breaker.assess(records=99, invalid=99).tripped
    assert breaker.assess(records=100, invalid=100).tripped


def test_just_under_the_threshold_is_left_alone():
    # 94 of 100 is terrible and worth an alert. It is not worth stopping the
    # machine, which is what RecordsBeingQuarantined is for.
    assert not breaker.assess(records=100, invalid=94).tripped
    assert breaker.assess(records=100, invalid=95).tripped


def test_it_can_be_switched_off_entirely(monkeypatch):
    monkeypatch.setattr(settings, "validation_breaker_enabled", False)
    assert not breaker.assess(records=1000, invalid=1000).tripped


def test_no_records_is_not_a_failure_rate():
    verdict = breaker.assess(records=0, invalid=0)
    assert verdict.rate == 0.0
    assert not verdict.tripped


def test_the_reason_names_the_dominant_rule():
    """Whoever reads this is being woken up to investigate; the first thing
    they need is what actually failed, not that something did."""
    verdict = breaker.assess(
        records=200, invalid=200,
        failed_rules={"email.syntax": 198, "record.has_identifier": 200},
    )
    assert verdict.tripped
    assert "record.has_identifier" in verdict.reason()
    assert "200 of 200" in verdict.reason()
    assert "100%" in verdict.reason()


def test_the_evidence_carries_the_thresholds_that_were_in_force():
    """A number without the threshold it was judged against cannot be argued
    with later, and the threshold is configurable."""
    evidence = breaker.assess(records=300, invalid=300).evidence()
    assert evidence["records"] == 300
    assert evidence["invalid"] == 300
    assert evidence["invalid_rate"] == 1.0
    assert evidence["threshold"] == 0.95
    assert evidence["minimum_records"] == 100


def test_dominant_rules_are_ordered_and_capped():
    verdict = breaker.assess(
        records=100, invalid=100,
        failed_rules={f"rule.{i}": i for i in range(1, 12)},
    )
    names = [rule for rule, _ in verdict.dominant_rules]
    assert names == ["rule.11", "rule.10", "rule.9", "rule.8", "rule.7"]
