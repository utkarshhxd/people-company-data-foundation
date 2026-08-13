"""Attribute-level validation rules, one module per concern.

Each module owns a single kind of value and exports a `RULES` tuple. This file
is the only place that knows which module applies to which value type, so adding
a rule means editing one small file about one subject rather than a single long
one about everything.

  base.py     shared vocabulary: severities, Judgement, AttributeContext
  email.py    syntax, length, role mailboxes, consumer domains
  phone.py    digit count, country code, filler, merged extensions
  url.py      hostname, LinkedIn, embedded credentials
  name.py     length, placeholders, drifted fields, completeness
  address.py  plausibility only — never format
  number.py   integer input fidelity, employee count, founding year
  postal.py   shape, lost leading zeros

The public surface is unchanged from when this was one module: `judge_attribute`
plus the shared types, so nothing downstream had to change.
"""

from validation.rules import address, email, name, number, phone, postal, url
from validation.rules.base import (
    DIGITS,
    FAIL,
    PASS,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    AttributeContext,
    Judgement,
    Rule,
    failed,
    passed,
    rule_normalization_outcome,
)

# Bumped from "1": address rules did not exist before, and several new rules
# were added to the existing types. A judgement is only meaningful relative to
# the rules that produced it, so results from the old ruleset stay queryable
# beside the new ones rather than being silently reinterpreted.
RULESET_VERSION = "2"

RULES_BY_VALUE_TYPE: dict[str, tuple[Rule, ...]] = {
    "email": email.RULES,
    "phone": phone.RULES,
    "url": url.RULES,
    "person_name": name.RULES,
    "address": address.RULES,
    "integer": number.RULES,
    "postal_code": postal.RULES,
}

# Identifiers, place names, region codes and free text still have no rules, and
# that remains deliberate: a vendor's own key, a town name and a SIC description
# are all legitimately arbitrary strings. Rules there would manufacture failures
# rather than find them. Addresses left this set in ruleset 2 — not because
# their format became checkable, but because their *plausibility* always was.
UNRULED_VALUE_TYPES = frozenset(
    {"text", "identifier", "place_name", "region_code", "lower_token"}
)

__all__ = [
    "AttributeContext", "Judgement", "Rule",
    "PASS", "FAIL", "DIGITS",
    "SEVERITY_ERROR", "SEVERITY_WARNING", "SEVERITY_INFO",
    "RULESET_VERSION", "RULES_BY_VALUE_TYPE", "UNRULED_VALUE_TYPES",
    "judge_attribute", "passed", "failed", "rule_normalization_outcome",
]


def judge_attribute(
    normalized_value: str | None, ctx: AttributeContext
) -> list[Judgement]:
    """All applicable judgements for one observation. Empty means no rule ran."""
    if normalized_value is None:
        outcome = rule_normalization_outcome(ctx.normalization_method, ctx.raw_value)
        return [outcome] if outcome else []

    judgements = []
    for rule in RULES_BY_VALUE_TYPE.get(ctx.value_type, ()):
        try:
            judgement = rule(normalized_value, ctx)
        except Exception as exc:
            # A broken rule must never take down a batch, and must never be
            # mistaken for a passing one.
            judgement = failed(
                "rule.error", SEVERITY_INFO,
                f"rule {rule.__name__} raised: {exc}", rule=rule.__name__,
            )
        if judgement is not None:
            judgements.append(judgement)
    return judgements
