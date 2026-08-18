"""Survivorship: which of several reported values becomes the trusted one.

There is no single correct rule, because fields differ in kind:

  an address CHANGES        - the newest report is the best one
  a founding year DOES NOT  - disagreement means someone is wrong, so count votes
  an email is an identifier - trust the source you trust most

Picking one global rule would be wrong for two thirds of the schema, so the rule
is chosen per field and recorded on every value it decides. A surprising golden
value must always be traceable to the policy that produced it.

Every rule is deterministic. Ties break on a fixed chain — reliability, then
recency, then source name, then the value itself — so rebuilding the same
observations always yields the same golden record. Without that, nothing
downstream is reproducible.
"""

from dataclasses import dataclass
from datetime import datetime

MOST_RELIABLE = "most_reliable_source"
MOST_RECENT = "most_recent"
MOST_FREQUENT = "most_frequent"
EARLIEST = "earliest"

# Trust the source we trust most. The sane default for identifiers and anything
# with no clear temporal or consensus behaviour.
DEFAULT_STRATEGY = MOST_RELIABLE

# Only the fields whose nature actually differs from the default are listed. A
# shorter table is easier to defend than an exhaustive one.
FIELD_STRATEGY: dict[tuple[str, str], str] = {
    # Things that genuinely change over time: the freshest report wins.
    ("person", "job_title"): MOST_RECENT,
    ("person", "department"): MOST_RECENT,
    ("person", "company_name"): MOST_RECENT,
    ("person", "address_line1"): MOST_RECENT,
    ("person", "address_line2"): MOST_RECENT,
    ("person", "city"): MOST_RECENT,
    ("person", "state_region"): MOST_RECENT,
    ("person", "postal_code"): MOST_RECENT,
    ("person", "email_status"): MOST_RECENT,
    ("company", "employee_count"): MOST_RECENT,
    ("company", "address_line1"): MOST_RECENT,
    ("company", "address_line2"): MOST_RECENT,
    ("company", "city"): MOST_RECENT,
    ("company", "state_region"): MOST_RECENT,
    ("company", "postal_code"): MOST_RECENT,
    ("company", "phone"): MOST_RECENT,

    # Stable facts. Disagreement means someone is wrong rather than out of date,
    # so consensus beats recency and beats trusting one source.
    ("person", "full_name"): MOST_FREQUENT,
    ("person", "first_name"): MOST_FREQUENT,
    ("person", "last_name"): MOST_FREQUENT,
    ("company", "company_name"): MOST_FREQUENT,
    ("company", "legal_name"): MOST_FREQUENT,
    ("company", "sic_code"): MOST_FREQUENT,
    ("company", "sic_description"): MOST_FREQUENT,
    ("company", "naics_code"): MOST_FREQUENT,

    # Immutable. A later source cannot know better when a company was founded,
    # so the earliest observation is preferred and disagreement is visible in
    # the evidence rather than resolved by recency.
    ("company", "founded_year"): EARLIEST,

    # The commercial profile. All of it moves, and none of it is settled by
    # consensus: two vendors reporting different revenue are usually reporting
    # different years, not contradicting each other, so the newest report wins
    # and the older one stays in the evidence. Funding totals only ever go up,
    # which makes recency the right rule for a second reason.
    ("company", "annual_revenue"): MOST_RECENT,
    ("company", "total_funding"): MOST_RECENT,
    ("company", "latest_funding_stage"): MOST_RECENT,
    ("company", "latest_funding_amount"): MOST_RECENT,
    ("company", "last_funding_date"): MOST_RECENT,
    ("company", "retail_location_count"): MOST_RECENT,
    ("company", "technologies"): MOST_RECENT,
    ("company", "keywords"): MOST_RECENT,
    ("company", "seo_description"): MOST_RECENT,
}

# Confidence in the chosen value. Built from source reliability — the one
# dimension that exists precisely to say how much a vendor is to be believed.
AGREEMENT_BONUS = 0.15      # per additional source reporting the same value
DISAGREEMENT_PENALTY = 0.10  # per additional source reporting a different one
MIN_CONFIDENCE = 0.05
MAX_CONFIDENCE = 0.99


@dataclass(frozen=True)
class Observation:
    """One source's claim about one field of one entity."""
    value: str
    raw_value: str
    source_id: str
    source_name: str
    reliability: float
    record_id: str
    observed_at: datetime


@dataclass
class Choice:
    value: str
    raw_value: str
    strategy: str
    confidence: float
    winning_record_id: str
    winning_source_id: str
    supporting_sources: int
    competing_values: int
    evidence: dict


def strategy_for(entity_type: str, canonical_field: str) -> str:
    return FIELD_STRATEGY.get((entity_type, canonical_field), DEFAULT_STRATEGY)


def _group_by_value(observations: list[Observation]) -> dict[str, list[Observation]]:
    groups: dict[str, list[Observation]] = {}
    for obs in observations:
        groups.setdefault(obs.value, []).append(obs)
    return groups


def _tiebreak_key(obs: Observation) -> tuple:
    """The fixed chain every rule falls back to, so no choice is ever arbitrary."""
    return (-obs.reliability, -obs.observed_at.timestamp(), obs.source_name, obs.value)


def _distinct_sources(observations: list[Observation]) -> int:
    return len({o.source_id for o in observations})


def _dissenting_sources(groups: dict[str, list[Observation]], winner_value: str) -> int:
    """How many distinct sources reported something other than the winning value.

    Sources, not values, and the distinction decides whether the number means
    anything. A vendor is routinely inconsistent with itself: an employer named
    by 1,247 of its contacts arrives with 1,247 assertions of that company's
    address and phone, differing in punctuation, in which office, in how stale
    the row is. Counting distinct values made that read as several hundred
    independent contradictions, and the penalty drove the best-attested
    companies to the confidence floor — exactly backwards, and the same mistake
    the most_frequent strategy already avoids by counting sources.

    One vendor disagreeing with itself is one vendor's messiness. Two vendors
    disagreeing is a genuine conflict, and only that should cost confidence.
    """
    return len({
        obs.source_id
        for value, group in groups.items() if value != winner_value
        for obs in group
    })


def _confidence(winner: Observation, supporting: int, dissenting: int) -> float:
    score = (
        winner.reliability
        + AGREEMENT_BONUS * (supporting - 1)
        - DISAGREEMENT_PENALTY * dissenting
    )
    return round(min(MAX_CONFIDENCE, max(MIN_CONFIDENCE, score)), 3)


def _pick(strategy: str, groups: dict[str, list[Observation]]) -> Observation:
    if strategy == MOST_FREQUENT:
        # Sort by how many distinct SOURCES back the value: one vendor sending
        # the same value in five files is one opinion, not five.
        best_value = min(
            groups,
            key=lambda v: (-_distinct_sources(groups[v]),
                           *_tiebreak_key(min(groups[v], key=_tiebreak_key))),
        )
        return min(groups[best_value], key=_tiebreak_key)

    everything = [obs for group in groups.values() for obs in group]
    if strategy == MOST_RECENT:
        return min(everything, key=lambda o: (-o.observed_at.timestamp(), *_tiebreak_key(o)))
    if strategy == EARLIEST:
        return min(everything, key=lambda o: (o.observed_at.timestamp(), *_tiebreak_key(o)))
    # MOST_RELIABLE, and the fallback for anything unrecognised.
    return min(everything, key=_tiebreak_key)


def choose(
    entity_type: str, canonical_field: str, observations: list[Observation]
) -> Choice | None:
    """Decide the golden value for one field of one entity."""
    usable = [o for o in observations if o.value]
    if not usable:
        return None

    strategy = strategy_for(entity_type, canonical_field)
    groups = _group_by_value(usable)
    winner = _pick(strategy, groups)

    supporting = _distinct_sources(groups[winner.value])
    # Two different counts, deliberately. competing_values is reported because
    # "eight distinct values competed" is a real fact about the field and worth
    # showing. dissenting is what confidence is built from, because how many
    # *sources* disagree is the question confidence is answering.
    competing = len(groups)
    dissenting = _dissenting_sources(groups, winner.value)

    # Everything that lost is kept, with who said it. Being beaten is not a
    # reason to disappear.
    alternatives = [
        {
            "value": value,
            "sources": sorted({o.source_name for o in group}),
            "supporting_sources": _distinct_sources(group),
        }
        for value, group in sorted(groups.items())
        if value != winner.value
    ]

    return Choice(
        value=winner.value,
        raw_value=winner.raw_value,
        strategy=strategy,
        confidence=_confidence(winner, supporting, dissenting),
        winning_record_id=winner.record_id,
        winning_source_id=winner.source_id,
        supporting_sources=supporting,
        competing_values=competing,
        evidence={
            "won_by": strategy,
            "winning_source": winner.source_name,
            "winning_source_reliability": winner.reliability,
            "agreeing_sources": sorted({o.source_name for o in groups[winner.value]}),
            "alternatives": alternatives,
        },
    )
