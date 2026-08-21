"""Ask the model to guess one missing field for one entity, from what's
already known about it.

Deliberately narrow, and narrower than it first was -- see ADR 0017. An
earlier version derived "eligible" by *excluding* contact/identity fields and
allowed everything else (addresses, revenue, funding, employee counts, free
text like SEO descriptions). A smoke test against the actual target model
(gemma3:4b) exposed why that was wrong on two counts:

  * asked to fill `seo_description` for a company it had almost no data on,
    it invented a fluent, plausible-sounding paragraph at stated confidence
    0.9. Open-ended text generation and hallucination are the same operation.
  * asked to fill `industry` or `seniority` from nothing but a company/person
    NAME -- including names made up for the test, describing nothing real --
    it still answered confidently. A small model's stated confidence does not
    track whether it actually has grounding.

Three responses to that evidence:

  * `ELIGIBLE_FIELDS` is now a small, explicit allowlist per entity type,
    chosen the way ADR 0014 chose its nine commercial-profile fields --
    named and justified, not derived by exclusion. Only bounded
    classification fields with a small answer space are in scope. Nothing
    with an open-ended free-text answer (descriptions, keywords, technology
    lists), and nothing numeric, spatial, or dated (revenue, funding,
    employee counts, addresses, dates) -- those are exactly ADR 0014's
    "producing facts" case.
  * Two further fields were dropped from that allowlist on a second pass,
    for a reason specific to them rather than to AI risk generally:
    `sic_description` and `seniority` are both defined in the canonical
    schema (see `common/canonical.py`) as "the SOURCE's own" classification
    -- a vendor-specific code or band, not a fact with one right answer to
    infer. Same for `company_category`. Asking a model to invent a vendor's
    internal segment code or banding scheme isn't a narrower version of
    enrichment, it's a category error: there is nothing to infer, because
    the field means "whatever this particular source called it."
  * `MIN_KNOWN_FIELDS` requires more than a bare name before the model is
    asked at all, since a bare name was enough for it to answer anyway.

None of this is the safety backstop, though -- ADR 0017 downgraded this from
"write an observation directly" to "file a proposal a human reviews" for
exactly this reason: a small model's stated confidence cannot be trusted as
the sole gate. These just cut down how much of what a human sees is obvious
noise.
"""

from common.ai_client import generate_json
from common.canonical import SELF, field_by_name

# Bounded classification fields with one right answer to infer -- not a
# vendor-specific code (sic_description, company_category, seniority are all
# defined in the canonical schema as "the SOURCE's own" classification, which
# a model has no way to match), and not open-ended, numeric, spatial or dated
# (see the module docstring for why those are excluded generally).
ELIGIBLE_FIELDS: dict[str, tuple[str, ...]] = {
    "company": ("industry",),
    "person": ("industry", "department"),
}

# Below this stated confidence, the model's own uncertainty says the guess
# isn't worth filing as a proposal at all.
MIN_CONFIDENCE = 0.55

# A single known fact -- often just the entity's own name -- was enough for
# the model to answer confidently anyway in testing. Requiring more forces at
# least some context beyond "this thing has a name" before it's asked.
MIN_KNOWN_FIELDS = 2


def ask_for_field(
    entity_type: str, canonical_field: str, known_values: dict[str, str]
) -> tuple[str, float, str] | None:
    """(value, stated_confidence, value_type) the model proposes, or None."""
    if canonical_field not in ELIGIBLE_FIELDS.get(entity_type, ()):
        return None
    spec = field_by_name(entity_type, canonical_field, SELF)
    if spec is None or len(known_values) < MIN_KNOWN_FIELDS:
        return None

    context = "\n".join(
        f"- {field}: {value}" for field, value in sorted(known_values.items())
    )
    prompt = (
        f"Known facts about this {entity_type}:\n{context}\n\n"
        f"Field to fill in: {canonical_field} ({spec.description})\n\n"
        'If you can confidently infer this field from the known facts, reply '
        '{"value": <string>, "confidence": <0-1>}. If you cannot, reply '
        '{"value": null, "confidence": 0}. Never invent an email, phone '
        "number, URL, or name that isn't directly implied by the facts above."
    )
    result = generate_json(
        prompt,
        system="You infer missing descriptive facts about companies and "
               "people from other facts already on file. You decline rather "
               "than guess when unsure.",
    )
    if not result or not result.get("value"):
        return None

    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if confidence < MIN_CONFIDENCE:
        return None

    return str(result["value"]), round(confidence, 3), spec.value_type
