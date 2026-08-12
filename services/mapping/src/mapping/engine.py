"""Decide which canonical field each source column represents.

Strategies run in order and each records its own method and confidence, so any
mapping can be explained afterwards: what matched, how well, and what was
rejected.
"""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from common.canonical import CANONICAL_SCHEMA_VERSION, fields_for, normalize_column_name

from mapping.detectors import match_ratio

AUTO_ACCEPT_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.60
# Values prove a column's TYPE, not which field it is, so value analysis alone
# can never reach the auto-accept threshold.
VALUE_ANALYSIS_CAP = 0.75
VALUE_ANALYSIS_MIN_RATIO = 0.60
CORROBORATION_BOOST = 0.15
MAX_ALTERNATIVES = 3
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


@dataclass
class Mapping:
    source_column: str
    canonical_field: str | None
    method: str
    confidence: float
    status: str
    evidence: dict[str, Any]
    schema_version: str = CANONICAL_SCHEMA_VERSION


def _alias_candidates(normalized: str, entity_type: str) -> list[Candidate]:
    out = []
    for spec in fields_for(entity_type):
        names = {normalize_column_name(spec.name)} | {
            normalize_column_name(alias) for alias in spec.aliases
        }
        if normalized in names:
            out.append(
                Candidate(spec.name, "exact_alias", 1.0, {"matched_alias": normalized})
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
                )
            )
    return out


def _similarity_candidates(normalized: str, entity_type: str) -> list[Candidate]:
    out = []
    for spec in fields_for(entity_type):
        best_ratio, best_against = 0.0, ""
        for target in {spec.name, *spec.aliases}:
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
                )
            )
    return out


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

    candidates = [*aliases, *similarities, *value_based]

    # Name evidence and value evidence agreeing is the only route by which a
    # non-exact match becomes trustworthy enough to auto-accept.
    by_value = {c.canonical_field: c for c in value_based}
    for candidate in [*aliases, *similarities]:
        corroborator = by_value.get(candidate.canonical_field)
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
                )
            )

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
        {"canonical_field": c.canonical_field, "method": c.method, "confidence": c.confidence}
        for c in candidates[1 : MAX_ALTERNATIVES + 1]
        if c.canonical_field != best.canonical_field
    ]
    evidence = {**best.detail, "alternatives": alternatives}

    if status == STATUS_UNMAPPED:
        evidence["reason"] = f"best confidence {best.confidence} below {REVIEW_THRESHOLD}"
        return Mapping(source_column, None, "none", best.confidence, status, evidence)

    return Mapping(source_column, best.canonical_field, best.method, best.confidence,
                   status, evidence)


def _resolve_collisions(mappings: list[Mapping]) -> list[Mapping]:
    """Two columns claiming the same field is ambiguous — send them all to review."""
    by_field: dict[str, list[Mapping]] = {}
    for mapping in mappings:
        if mapping.canonical_field is not None:
            by_field.setdefault(mapping.canonical_field, []).append(mapping)

    for canonical_field, claimants in by_field.items():
        if len(claimants) < 2:
            continue
        competing = [m.source_column for m in claimants]
        for mapping in claimants:
            mapping.evidence = {
                **mapping.evidence,
                "collision": {
                    "canonical_field": canonical_field,
                    "competing_columns": competing,
                },
            }
            if mapping.status == STATUS_AUTO_ACCEPTED:
                mapping.status = STATUS_NEEDS_REVIEW
    return mappings


def map_columns(
    columns: list[str], entity_type: str, samples: dict[str, list[str]]
) -> list[Mapping]:
    mappings = [
        map_column(column, entity_type, samples.get(column, [])) for column in columns
    ]
    return _resolve_collisions(mappings)
