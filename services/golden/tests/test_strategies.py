from datetime import UTC, datetime, timedelta

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

BASE = datetime(2026, 1, 1, tzinfo=UTC)


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


# --------------------------------------------------------------------------
# confidence counts sources, not values
# --------------------------------------------------------------------------

def test_one_vendor_disagreeing_with_itself_costs_one_penalty():
    """An employer named by a thousand of its own contacts is still one vendor.

    Apollo supplies its 1,247 AdventHealth contacts with 760 slightly different
    company ids. Counting distinct values made that 759 contradictions and drove
    confidence to the floor, so the best-attested company in the database read
    as the least certain. It is one vendor being inconsistent with itself.
    """
    observations = [obs(f"id-{i}", "apollo", reliability=0.85) for i in range(200)]
    choice = choose("company", "company_external_id", observations)
    # 200 distinct values, but only one source, so one penalty.
    assert choice.competing_values == 200
    assert choice.confidence == pytest.approx(0.75, abs=0.001)


def test_two_vendors_disagreeing_costs_two():
    """A genuine conflict between sources is what confidence should reflect."""
    observations = [
        obs("Acme Ltd", "vendor_a", reliability=0.9),
        obs("Acme Limited", "vendor_b", reliability=0.5),
        obs("ACME", "vendor_c", reliability=0.5),
    ]
    choice = choose("company", "legal_name", observations)
    assert choice.value == "Acme Ltd"
    # Two other sources dissent: 0.9 - 0.10 - 0.10
    assert choice.confidence == pytest.approx(0.70, abs=0.001)


def test_unanimous_sources_are_not_penalised():
    observations = [
        obs("Acme", "vendor_a", reliability=0.7),
        obs("Acme", "vendor_b", reliability=0.7),
    ]
    choice = choose("company", "legal_name", observations)
    # One agreeing source beyond the winner, nobody dissenting.
    assert choice.supporting_sources == 2
    assert choice.confidence == pytest.approx(0.85, abs=0.001)


def test_a_source_backing_the_winner_does_not_also_count_against_it():
    """A vendor that reported the winner AND something else still dissents once.

    It is not double-counted, and it is not excused either: it did report a
    competing value, and that is worth one penalty however many it reported.
    """
    observations = [
        obs("Acme", "vendor_a", reliability=0.8),
        obs("Acme Corp", "vendor_a", reliability=0.8),
        obs("Acme", "vendor_b", reliability=0.6),
    ]
    choice = choose("company", "legal_name", observations)
    assert choice.value == "Acme"
    # vendor_a and vendor_b agree on the winner (+0.15); vendor_a also dissents (-0.10)
    assert choice.confidence == pytest.approx(0.85, abs=0.001)


def test_competing_values_still_reports_every_value_that_lost():
    """Confidence changed; what is shown to a human did not.

    'Eight distinct values competed' is a real fact about the field and stays
    visible, even though it is no longer what the score is built from.
    """
    observations = [obs(f"v{i}", "one_vendor", reliability=0.8) for i in range(8)]
    choice = choose("company", "legal_name", observations)
    assert choice.competing_values == 8
    assert len(choice.evidence["alternatives"]) == 7


# --------------------------------------------------------------------------
# the commercial profile: everything about it moves


@pytest.mark.parametrize(
    "field",
    ["annual_revenue", "total_funding", "latest_funding_stage",
     "latest_funding_amount", "last_funding_date", "retail_location_count",
     "technologies", "keywords", "seo_description"],
)
def test_commercial_facts_take_the_newest_report(field):
    assert strategy_for("company", field) == MOST_RECENT


def test_two_revenue_figures_are_different_years_not_a_contradiction():
    """Consensus would be wrong here. A vendor reporting last year's revenue is
    not disagreeing with one reporting this year's, so the newest wins and the
    older stays in the evidence rather than voting against it."""
    choice = choose("company", "annual_revenue", [
        obs("12000000", "vendor_a", reliability=0.9, days=0),
        obs("15000000", "vendor_b", reliability=0.4, days=200),
    ])
    assert choice.value == "15000000"
    assert choice.strategy == MOST_RECENT
    assert choice.competing_values == 2


def test_a_value_longer_than_a_btree_tuple_is_still_chooseable():
    """Technologies cells reach ~2,960 characters. Nothing in survivorship may
    care, and the golden index is a prefix index precisely so this can be
    stored — see migration 0017."""
    long_value = ", ".join(f"technology-{n}" for n in range(300))
    assert len(long_value) > 2704
    choice = choose("company", "technologies", [obs(long_value, "apollo")])
    assert choice.value == long_value
