"""Shared vocabulary for every rule module.

A rule asks one question: *is this value usable as the canonical field it was
mapped to?* It never answers "is this value correct" (nothing here can know
that) and it never changes the value.

Three severities, and the difference matters because it decides whether a record
is merely suspicious or actually unusable:

  error   - the value cannot serve as this field at all. An email with no '@'
            is not a weak email, it is not an email.
  warning - plausible but suspect. A phone with no country code is usable; it
            is just ambiguous internationally.
  info    - an observation worth recording, no judgement implied.

Rules return None when they do not apply, which is different from passing. That
distinction is preserved all the way into the database, because "no rule ran"
and "every rule passed" are very different statements about a record.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

PASS = "pass"
FAIL = "fail"


@dataclass(frozen=True)
class Judgement:
    rule_id: str
    severity: str
    outcome: str
    message: str | None = None
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AttributeContext:
    canonical_field: str
    entity_type: str
    value_type: str
    raw_value: str
    normalization_method: str


Rule = Callable[[str, AttributeContext], Judgement | None]


def passed(rule_id: str, severity: str, **details) -> Judgement:
    return Judgement(rule_id, severity, PASS, None, details)


def failed(rule_id: str, severity: str, message: str, **details) -> Judgement:
    return Judgement(rule_id, severity, FAIL, message, details)


# Shared across several rule modules; defined once so "how many digits" means
# the same thing in a phone rule as in a name rule.
DIGITS = re.compile(r"\d")


def rule_normalization_outcome(method: str, raw_value: str) -> Judgement | None:
    """Runs when there is no normalized value but the source did write something.

    ':failed' means the normalizer raised; ':empty' means it consumed the whole
    value — 'abc' in a phone column leaves zero digits behind. Either way the
    source wrote a value that cannot serve as this field.

    Lives here rather than in a type module because it is about the value's
    existence, not about what type it was meant to be.
    """
    if method.endswith(":failed"):
        return failed("value.normalization_failed", SEVERITY_ERROR,
                      "the value could not be normalized for this field type",
                      raw_value=raw_value, method=method)
    if method.endswith(":empty"):
        return failed("value.empty_after_normalization", SEVERITY_ERROR,
                      "nothing usable remained once the value was normalized",
                      raw_value=raw_value, method=method)
    if method.endswith(":range"):
        # A warning, not an error: the source said something true and useful —
        # that it does not know the exact figure. We have no field shaped to
        # hold that, which is our limitation rather than the vendor's mistake.
        return failed("value.range_given", SEVERITY_WARNING,
                      "the source gave a range where a single number was "
                      "required, so no value was stored",
                      raw_value=raw_value, method=method)
    return None
