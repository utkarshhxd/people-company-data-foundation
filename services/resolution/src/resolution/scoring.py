"""Decide whether a record is an observation of an entity we already know.

The spec's instruction is "do not blindly merge records", and the shape of this
module is that instruction made mechanical:

  * a moderate key can never reach the auto-link threshold, no matter how many
    moderate keys agree. Three people at one company share a name pattern, a
    city and a switchboard number; none of that makes them one person.
  * corroboration adds confidence but is capped, so agreement between weak
    signals never impersonates a strong one.
  * anything in between is not silently guessed. The record gets its own entity
    and a candidate is filed for a human, which keeps the pipeline moving
    without inventing a merge nobody approved.

Match confidence is its own dimension. It says nothing about whether the values
are valid, whether the columns were mapped correctly, or whether the vendor is
reliable — those are answered elsewhere and must not be folded in here.
"""

from dataclasses import dataclass, field

from resolution.keys import KEY_WEIGHTS, STRONG, IdentityKey

AUTO_LINK_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.60
# Each additional agreeing key type adds this much. Small on purpose: the second
# signal should tip a borderline decision, not manufacture certainty.
CORROBORATION_BOOST = 0.04
MAX_CONFIDENCE = 0.99

DECISION_LINK = "link"
DECISION_REVIEW = "review"
DECISION_NEW = "new_entity"


@dataclass
class Match:
    entity_id: str
    confidence: float
    method: str
    matched_keys: list[tuple[str, str]] = field(default_factory=list)

    @property
    def evidence(self) -> dict:
        return {
            "matched_keys": [
                {"key_type": t, "key_value": v} for t, v in self.matched_keys
            ],
            "strongest_key": self.method,
        }


@dataclass
class Decision:
    decision: str
    match: Match | None
    # Every candidate considered, not just the winner — a link is only
    # explainable if what it beat is recorded too.
    considered: list[Match] = field(default_factory=list)


def score_candidate(
    entity_type: str, entity_id: str, matched: list[IdentityKey]
) -> Match | None:
    """Confidence that this record and this entity are the same real-world thing."""
    if not matched:
        return None

    weights = KEY_WEIGHTS[entity_type]
    ranked = sorted(matched, key=lambda k: weights.get(k.key_type, 0.0), reverse=True)
    best = ranked[0]
    confidence = weights.get(best.key_type, 0.0)

    # Distinct key TYPES, not distinct values: three phone numbers agreeing is
    # one kind of evidence repeated, not three kinds.
    extra_types = {k.key_type for k in ranked} - {best.key_type}
    confidence += CORROBORATION_BOOST * len(extra_types)

    if best.strength != STRONG:
        # Hard ceiling. Without it, enough moderate agreement would eventually
        # cross the auto-link line and merge two people who merely work together.
        confidence = min(confidence, AUTO_LINK_THRESHOLD - 0.01)

    return Match(
        entity_id=entity_id,
        confidence=round(min(confidence, MAX_CONFIDENCE), 3),
        method=best.key_type,
        matched_keys=[(k.key_type, k.key_value) for k in ranked],
    )


def decide(
    entity_type: str, candidates: dict[str, list[IdentityKey]]
) -> Decision:
    """Turn per-entity key overlaps into one decision."""
    scored = [
        match
        for entity_id, matched in candidates.items()
        if (match := score_candidate(entity_type, entity_id, matched)) is not None
    ]
    if not scored:
        return Decision(DECISION_NEW, None, [])

    scored.sort(key=lambda m: m.confidence, reverse=True)
    best = scored[0]

    # Two entities matching equally well is itself a reason not to act: picking
    # one silently would be a coin flip, and the tie usually means those two
    # entities are duplicates of each other.
    tied = [m for m in scored[1:] if m.confidence == best.confidence]
    if tied and best.confidence >= AUTO_LINK_THRESHOLD:
        return Decision(DECISION_REVIEW, best, scored)

    if best.confidence >= AUTO_LINK_THRESHOLD:
        return Decision(DECISION_LINK, best, scored)
    if best.confidence >= REVIEW_THRESHOLD:
        return Decision(DECISION_REVIEW, best, scored)
    return Decision(DECISION_NEW, best, scored)
