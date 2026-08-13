from datetime import datetime, timedelta, timezone

import pytest
from golden.strategies import (
    EARLIEST,
    MOST_FREQUENT,
    MOST_RECENT,
    MOST_RELIABLE,
    Observation,
    choose,
    strategy_for,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def obs(value, source, reliability=0.5, days=0, record=None):
    return Observation(
        value=value,
        raw_value=value,
        source_id=f"id-{source}",
        source_name=source,
        reliability=reliability,
        record_id=record or f"rec-{source}-{value}",
        observed_at=BASE + timedelta(days=days),
    )


# --------------------------------------------------------------------------
# the rule is chosen per field, because fields differ in kind
# --------------------------------------------------------------------------

def test_volatile_fields_take_the_newest_report():
    assert strategy_for("company", "address_line1") == MOST_RECENT
    assert strategy_for("person", "job_title") == MOST_RECENT


def test_stable_fields_take_the_consensus():
    assert strategy_for("company", "company_name") == MOST_FREQUENT
    assert strategy_for("person", "full_name") == MOST_FREQUENT


def test_immutable_fields_prefer_the_earliest_observation():
    assert strategy_for("company", "founded_year") == EARLIEST


def test_anything_unlisted_falls_back_to_trusting_the_best_source():
    assert strategy_for("company", "website") == MOST_RELIABLE
    assert strategy_for("person", "email") == MOST_RELIABLE


# --------------------------------------------------------------------------
# the rules themselves
# --------------------------------------------------------------------------

def test_an_address_that_changed_takes_the_newest_value():
    """A moved office is not a conflict — the older report was simply true then."""
    choice = choose("company", "address_line1", [
        obs("1 Old Street", "vendor_a", 0.9, days=0),
        obs("2 New Street", "vendor_b", 0.4, days=30),
    ])
    assert choice.value == "2 New Street"
    assert choice.strategy == MOST_RECENT


def test_a_company_name_takes_the_consensus_not_the_newest():
    """Disagreement about a stable fact means someone is wrong, so count votes."""
    choice = choose("company", "company_name", [
        obs("Asia Foundation", "vendor_dc", 0.7, days=0),
        obs("The Asia Foundation", "vendor_b", 0.6, days=10),
        obs("Asia Foundation", "vendor_near", 0.5, days=20),
    ])
    assert choice.value == "Asia Foundation"
    assert choice.supporting_sources == 2
    assert choice.competing_values == 2


def test_one_vendor_repeating_itself_is_one_opinion_not_many():
    """Otherwise a chatty low-quality vendor could outvote a careful one."""
    choice = choose("company", "company_name", [
        obs("Loud Corp", "vendor_a", 0.4, days=0, record="r1"),
        obs("Loud Corp", "vendor_a", 0.4, days=1, record="r2"),
        obs("Loud Corp", "vendor_a", 0.4, days=2, record="r3"),
        obs("Quiet Corp", "vendor_b", 0.9, days=3, record="r4"),
    ])
    assert choice.supporting_sources == 1
    assert choice.value == "Quiet Corp"


def test_the_most_trusted_source_wins_an_identifier():
    choice = choose("company", "website", [
        obs("old.example.com", "vendor_a", 0.3, days=10),
        obs("new.example.com", "vendor_b", 0.9, days=0),
    ])
    assert choice.value == "new.example.com"
    assert choice.strategy == MOST_RELIABLE


def test_a_founding_year_prefers_the_first_thing_we_were_told():
    """A later vendor cannot know better when a company was founded."""
    choice = choose("company", "founded_year", [
        obs("1954", "vendor_a", 0.5, days=0),
        obs("1955", "vendor_b", 0.9, days=30),
    ])
    assert choice.value == "1954"
    assert choice.strategy == EARLIEST


# --------------------------------------------------------------------------
# confidence, and what losing values are owed
# --------------------------------------------------------------------------

def test_confidence_starts_from_how_much_the_winning_source_is_trusted():
    choice = choose("company", "website", [obs("x.com", "vendor_a", 0.7)])
    assert choice.confidence == 0.7


def test_agreement_raises_confidence_and_conflict_lowers_it():
    agreed = choose("company", "sic_code", [
        obs("861102", "vendor_a", 0.7), obs("861102", "vendor_b", 0.7),
    ])
    contested = choose("company", "sic_code", [
        obs("861102", "vendor_a", 0.7), obs("999999", "vendor_b", 0.7),
    ])
    assert agreed.confidence > 0.7
    assert contested.confidence < 0.7


def test_confidence_is_never_absolute():
    choice = choose("company", "sic_code", [obs("1", f"v{i}", 0.9) for i in range(20)])
    assert choice.confidence <= 0.99


def test_every_rejected_value_is_kept_with_who_said_it():
    """Being beaten is not a reason to disappear."""
    choice = choose("company", "company_name", [
        obs("Asia Foundation", "vendor_dc", 0.7),
        obs("Asia Foundation", "vendor_near", 0.5),
        obs("The Asia Foundation", "vendor_b", 0.6),
    ])
    assert choice.evidence["alternatives"] == [
        {"value": "The Asia Foundation", "sources": ["vendor_b"], "supporting_sources": 1}
    ]
    assert choice.evidence["agreeing_sources"] == ["vendor_dc", "vendor_near"]


def test_the_deciding_rule_is_recorded_on_the_value():
    """A surprising golden value must trace to a policy, not to a mystery."""
    choice = choose("person", "job_title", [obs("CTO", "vendor_a")])
    assert choice.evidence["won_by"] == MOST_RECENT
    assert choice.evidence["winning_source"] == "vendor_a"


# --------------------------------------------------------------------------
# determinism and edges
# --------------------------------------------------------------------------

@pytest.mark.parametrize("strategy_field", ["company_name", "website", "founded_year"])
def test_the_same_observations_always_produce_the_same_answer(strategy_field):
    """Rebuilding must be reproducible, or nothing downstream can be trusted."""
    observations = [
        obs("alpha", "vendor_a", 0.5, days=1),
        obs("beta", "vendor_b", 0.5, days=1),
    ]
    first = choose("company", strategy_field, observations)
    second = choose("company", strategy_field, list(reversed(observations)))
    assert first.value == second.value


def test_no_usable_observation_produces_no_golden_value():
    assert choose("company", "website", []) is None
    assert choose("company", "website", [obs("", "vendor_a")]) is None


def test_a_single_source_is_uncontested_not_unanimous():
    choice = choose("company", "website", [obs("x.com", "vendor_a", 0.6)])
    assert choice.supporting_sources == 1
    assert choice.competing_values == 1
    assert choice.evidence["alternatives"] == []
