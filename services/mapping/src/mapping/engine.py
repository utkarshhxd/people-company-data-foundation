"""Decide which canonical field each source column represents.

Strategies run in order and each records its own method and confidence, so any
mapping can be explained afterwards: what matched, how well, and what was
rejected.
"""

import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from common.ai_client import generate_json
from common.canonical import (
    CANONICAL_SCHEMA_VERSION,
    SELF,
    fields_for,
    normalize_column_name,
)
from common.config import settings

from mapping.detectors import (
    DETECTOR_SPECIFICITY,
    DISCRIMINATING_DETECTORS,
    match_ratio,
)

logger = logging.getLogger(__name__)

AUTO_ACCEPT_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.60
# Values prove a column's TYPE, not which field it is, so value analysis alone
# can never reach the auto-accept threshold.
VALUE_ANALYSIS_CAP = 0.75
VALUE_ANALYSIS_MIN_RATIO = 0.60
CORROBORATION_BOOST = 0.15
MAX_ALTERNATIVES = 3
# Same cap and same reasoning as value analysis: a model naming a field is a
# proposal, never a confirmation, so it can never on its own reach auto-accept.
AI_SUGGESTION_CAP = 0.75
# Below this stated confidence, the model's own uncertainty says the guess
# isn't worth putting in front of a reviewer at all.
AI_SUGGESTION_MIN_CONFIDENCE = 0.50
# Above fuzzy-similarity noise (a curated alias is better evidence than a
# difflib ratio) but below AUTO_ACCEPT_THRESHOLD, because an ambiguous alias
# must always reach a human. _status_for is not trusted to keep that promise on
# confidence alone — map_column pins the status explicitly.
AMBIGUOUS_ALIAS_CONFIDENCE = 0.85

STATUS_AUTO_ACCEPTED = "auto_accepted"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_UNMAPPED = "unmapped"


@dataclass
class Candidate:
    canonical_field: str
    method: str
    confidence: float
    detail: dict[str, Any] = field(default_factory=dict)
    # Whose attribute this candidate would make the column. Two candidates
    # naming the same field for different subjects are different answers, not
    # competing ones.
    subject: str = SELF


@dataclass
class Mapping:
    source_column: str
    canonical_field: str | None
    method: str
    confidence: float
    status: str
    evidence: dict[str, Any]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    subject: str = SELF


def _alias_candidates(normalized: str, entity_type: str) -> list[Candidate]:
    out = []
    for spec in fields_for(entity_type):
        names = {normalize_column_name(name) for name in spec.match_names}
        if normalized in names:
            out.append(
                Candidate(spec.name, "exact_alias", 1.0,
                          {"matched_alias": normalized}, spec.subject)
            )
            continue
        # Plausible but not certain: propose the field, but capped below the
        # auto-accept threshold so a human confirms what the column really means.
        ambiguous = {normalize_column_name(a) for a in spec.ambiguous_aliases}
        if normalized in ambiguous:
            out.append(
                Candidate(
                    spec.name,
                    "ambiguous_alias",
                    AMBIGUOUS_ALIAS_CONFIDENCE,
                    {"matched_ambiguous_alias": normalized,
                     "note": "alias is plausible but not decisive; needs confirmation"},
                    spec.subject,
                )
            )
    return out


def _similarity_candidates(normalized: str, entity_type: str) -> list[Candidate]:
    out = []
    for spec in fields_for(entity_type):
        best_ratio, best_against = 0.0, ""
        # match_names, not the raw name: an employer field is reachable only
        # through its qualified aliases, and fuzzy matching its bare name would
        # let 'city' half-match the employer's city as well as the person's.
        for target in spec.match_names:
            ratio = SequenceMatcher(None, normalized, normalize_column_name(target)).ratio()
            if ratio > best_ratio:
                best_ratio, best_against = ratio, target
        if best_ratio >= REVIEW_THRESHOLD:
            out.append(
                Candidate(
                    spec.name,
                    "similarity",
                    round(best_ratio, 3),
                    {"closest_to": best_against, "ratio": round(best_ratio, 3)},
                    spec.subject,
                )
            )
    return out


def _value_candidates(values: list[str], entity_type: str) -> list[Candidate]:
    if not values:
        return []
    out = []
    for spec in fields_for(entity_type):
        if spec.detector is None:
            continue
        ratio = match_ratio(spec.detector, values)
        if ratio >= VALUE_ANALYSIS_MIN_RATIO:
            out.append(
                Candidate(
                    spec.name,
                    "value_analysis",
                    round(min(VALUE_ANALYSIS_CAP, ratio), 3),
                    {"detector": spec.detector, "match_ratio": round(ratio, 3),
                     "sampled": len(values)},
                    spec.subject,
                )
            )
    return out


def _ai_candidates(source_column: str, entity_type: str, values: list[str]) -> list[Candidate]:
    """Ask the local model for a field guess, for a column the deterministic
    rules alone left below auto-accept.

    Only ever called there -- see the guard in `map_column` -- so a well-mapped
    file never pays for a model call, and the AI's role stays exactly what the
    rest of this module already enforces for every other soft signal: propose,
    never confirm. The proposal is capped below AUTO_ACCEPT_THRESHOLD and always
    lands in needs_review, same queue a human already works.
    """
    specs = fields_for(entity_type)
    catalogue = "\n".join(f"- {spec.name} ({spec.subject}): {spec.description}"
                          for spec in specs)
    sample = ", ".join(repr(v) for v in values[:8] if v)
    prompt = (
        f"Source column name: {source_column!r}\n"
        f"Sample values: {sample or '(none)'}\n\n"
        f"Canonical fields for entity type {entity_type!r}:\n{catalogue}\n\n"
        'Which canonical field does this column most likely represent? Reply '
        'with JSON: {"field": <name or null>, "confidence": <0-1>, "reason": '
        '<short string>}. Use null if none of the fields fit.'
    )
    result = generate_json(
        prompt,
        system="You map messy CRM/vendor column names onto a fixed schema. "
               "You are cautious: you only name a field when reasonably sure, "
               "and answer null otherwise.",
    )
    if not result or not result.get("field"):
        return []

    spec = next((s for s in specs if s.name == result["field"]), None)
    if spec is None:
        return []

    try:
        stated = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        return []
    if stated < AI_SUGGESTION_MIN_CONFIDENCE:
        return []

    confidence = round(min(AI_SUGGESTION_CAP, max(0.0, stated)), 3)
    return [
        Candidate(
            spec.name, "ai_suggestion", confidence,
            {"model": settings.ollama_model, "stated_confidence": stated,
             "reason": result.get("reason", "")},
            spec.subject,
        )
    ]


def _status_for(confidence: float) -> str:
    if confidence >= AUTO_ACCEPT_THRESHOLD:
        return STATUS_AUTO_ACCEPTED
    if confidence >= REVIEW_THRESHOLD:
        return STATUS_NEEDS_REVIEW
    return STATUS_UNMAPPED


def map_column(source_column: str, entity_type: str, values: list[str]) -> Mapping:
    normalized = normalize_column_name(source_column)
    aliases = _alias_candidates(normalized, entity_type)
    similarities = _similarity_candidates(normalized, entity_type)
    value_based = _value_candidates(values, entity_type)

    # Values may *corroborate* any name-based candidate, but may only
    # *originate* one when the detector identifies a family of field rather
    # than merely a shape of value. Otherwise a column of numbers proposes
    # whichever numeric field happens to carry the integer detector, which is a
    # guess wearing the costume of evidence — and worse than no proposal,
    # because a reviewer reading a plausible field name tends to accept it.
    # Values also never reveal *whose* attribute something is: a phone number
    # looks identical whether it is the person's or their employer's. So an
    # employer field is reachable only by a name that says so, never by values.
    originating = [
        c for c in value_based
        if c.detail.get("detector") in DISCRIMINATING_DETECTORS and c.subject == SELF
    ]
    # Keep only the narrowest kind of match that fired. A LinkedIn URL matches
    # the url detector too, and letting both propose leaves a real signal tied
    # with a vaguer one at the same capped confidence.
    if originating:
        finest = max(
            DETECTOR_SPECIFICITY.get(c.detail.get("detector"), 0) for c in originating
        )
        originating = [
            c for c in originating
            if DETECTOR_SPECIFICITY.get(c.detail.get("detector"), 0) == finest
        ]
    candidates = [*aliases, *similarities, *originating]

    # Name evidence and value evidence agreeing is the only route by which a
    # non-exact match becomes trustworthy enough to auto-accept. Keyed by field
    # *and* subject, so the employer's website cannot be corroborated by values
    # that were evidence for the person's own.
    by_value = {(c.canonical_field, c.subject): c for c in value_based}
    for candidate in [*aliases, *similarities]:
        corroborator = by_value.get((candidate.canonical_field, candidate.subject))
        # An ambiguous alias is excluded: values can confirm a column's TYPE but
        # never resolve which field an ambiguous name meant, so corroboration
        # must not be allowed to push it over the auto-accept line.
        if corroborator is not None and candidate.method not in (
            "exact_alias", "ambiguous_alias"
        ):
            candidates.append(
                Candidate(
                    candidate.canonical_field,
                    "corroborated",
                    round(min(0.95, candidate.confidence + CORROBORATION_BOOST), 3),
                    {**candidate.detail, **corroborator.detail, "corroborated": True},
                    candidate.subject,
                )
            )

    # The model is only ever asked about a column the deterministic rules
    # couldn't already place with confidence -- an exact alias needs no second
    # opinion, and asking for one on every column would turn one model call per
    # unique layout into one per column of it, for no gain.
    deterministic_best = max((c.confidence for c in candidates), default=0.0)
    if settings.ai_mapping_enabled and deterministic_best < AUTO_ACCEPT_THRESHOLD:
        candidates.extend(_ai_candidates(source_column, entity_type, values))

    if not candidates:
        return Mapping(
            source_column, None, "none", 0.0, STATUS_UNMAPPED,
            {"reason": "no alias, similarity, or value evidence"},
        )

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    best = candidates[0]
    status = _status_for(best.confidence)
    # An ambiguous alias names the likely field but is never decisive on its
    # own, whatever its score works out to.
    if best.method == "ambiguous_alias":
        status = STATUS_NEEDS_REVIEW

    alternatives = [
        {"canonical_field": c.canonical_field, "method": c.method,
         "confidence": c.confidence, "subject": c.subject}
        for c in candidates[1 : MAX_ALTERNATIVES + 1]
        if (c.canonical_field, c.subject) != (best.canonical_field, best.subject)
    ]
    evidence = {**best.detail, "alternatives": alternatives}

    if status == STATUS_UNMAPPED:
        evidence["reason"] = f"best confidence {best.confidence} below {REVIEW_THRESHOLD}"
        return Mapping(source_column, None, "none", best.confidence, status, evidence)

    return Mapping(source_column, best.canonical_field, best.method, best.confidence,
                   status, evidence, subject=best.subject)


# How decisive each kind of evidence is about *which field* a column is.
# A curated alias naming the field outright and a detector inferring one from
# value shape are not competing claims of equal standing, and scoring them on
# confidence alone lets the weaker veto the stronger.
_METHOD_RANK = {
    "exact_alias": 3,
    "corroborated": 2,
    "ambiguous_alias": 1,
    "similarity": 1,
    "value_analysis": 0,
    "ai_suggestion": 0,
}


def _resolve_collisions(mappings: list[Mapping]) -> list[Mapping]:
    """Settle two columns claiming the same canonical field.

    Two columns making the *same kind* of claim is genuine ambiguity and every
    claimant goes to a human — a file with both `name` and `org_name` must not
    let the higher score silently win. But when one claimant's evidence is
    strictly better in kind, it takes the field and the rest give it up:
    `phone` matching the alias exactly should not be dragged into review
    because `domain_expiration` holds digits that look phone-shaped.

    A column that loses is recorded as unmapped rather than reassigned. Its
    values are still captured as observations — nothing is lost — but claiming
    a field it did not win would attribute data to the wrong place.
    """
    # Keyed by field *and* subject. 'City' and 'Company City' both answer
    # `city`, and they are not in competition — they describe different
    # subjects, and both values must survive.
    by_field: dict[tuple[str, str], list[Mapping]] = {}
    for mapping in mappings:
        if mapping.canonical_field is not None:
            by_field.setdefault((mapping.canonical_field, mapping.subject), []).append(
                mapping
            )

    for (canonical_field, subject), claimants in by_field.items():
        if len(claimants) < 2:
            continue
        competing = [m.source_column for m in claimants]
        best_rank = max(_METHOD_RANK.get(m.method, 0) for m in claimants)
        winners = [m for m in claimants if _METHOD_RANK.get(m.method, 0) == best_rank]
        # Identity, not equality: Mapping is a dataclass, so two columns that
        # happened to produce identical mappings would compare equal and both
        # be treated as the winner.
        winner_ids = {id(m) for m in winners}

        for mapping in claimants:
            collision = {
                "canonical_field": canonical_field,
                "subject": subject,
                "competing_columns": competing,
            }
            if id(mapping) in winner_ids:
                if len(winners) > 1:
                    # Equally good claims on the same field. Nobody wins by
                    # score; a human decides.
                    collision["outcome"] = "contested"
                    mapping.evidence = {**mapping.evidence, "collision": collision}
                    if mapping.status == STATUS_AUTO_ACCEPTED:
                        mapping.status = STATUS_NEEDS_REVIEW
                else:
                    collision["outcome"] = "held"
                    collision["beat"] = [
                        m.source_column for m in claimants if id(m) not in winner_ids
                    ]
                    mapping.evidence = {**mapping.evidence, "collision": collision}
            else:
                collision["outcome"] = "yielded"
                collision["lost_to"] = [m.source_column for m in winners]
                collision["would_have_been"] = canonical_field
                mapping.canonical_field = None
                mapping.method = "none"
                mapping.status = STATUS_UNMAPPED
                mapping.subject = SELF
                mapping.evidence = {**mapping.evidence, "collision": collision}
    return mappings


def map_columns(
    columns: list[str], entity_type: str, samples: dict[str, list[str]]
) -> list[Mapping]:
    mappings = [
        map_column(column, entity_type, samples.get(column, [])) for column in columns
    ]
    return _resolve_collisions(mappings)
