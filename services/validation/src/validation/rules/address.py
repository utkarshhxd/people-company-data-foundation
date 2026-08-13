"""Address rules.

Addresses had no rules until now, and the reasoning for that was sound as far as
it went: any string can legitimately be a street address, formats vary wildly
between countries, and normalization deliberately only collapses whitespace
because punctuation carries meaning (`#610`, `Ave NW`).

That argument rules out *format* rules. It does not rule out *plausibility*
rules, which is what these are. Every one is a warning, never an error, and none
of them can reject an address for being foreign, unusual, or punctuated oddly —
they flag the specific ways an address column goes wrong in practice:

  * placeholder text that survived normalization ("same as above")
  * whitespace lost upstream (`1720WisconsinAveNW`, seen in real vendor data)
  * a value too short to locate anything
  * a value that is plainly a different field that drifted into this column

The DIGITS check is deliberately `info`, not `warning`: plenty of real addresses
have no street number at all.
"""

import re

from validation.rules.base import (
    DIGITS,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    AttributeContext,
    Judgement,
    failed,
    passed,
)

MIN_LENGTH = 5

# Text that means "no address here" but is not a null token, so it arrives with
# a normalized value and would otherwise be treated as a real address.
PLACEHOLDER_ADDRESSES = {
    "same as above", "see above", "as above", "same", "address", "street",
    "no address", "not provided", "not given", "tbd", "todo", "xxx", "n a",
    "undisclosed", "confidential", "withheld", "na na",
}

# A long token mixing letters and digits with no spaces is what a lost delimiter
# looks like: '1720WisconsinAveNW' is one token but four words.
RUN_TOGETHER_MIN = 12
_HAS_LOWER_UPPER_RUN = re.compile(r"[a-z][A-Z]")


def rule_length(value: str, ctx: AttributeContext) -> Judgement | None:
    if len(value) >= MIN_LENGTH:
        return passed("address.length", SEVERITY_WARNING, length=len(value))
    return failed(
        "address.length", SEVERITY_WARNING,
        f"{len(value)} characters is too short to locate an address",
        value=value, length=len(value),
    )


def rule_placeholder(value: str, ctx: AttributeContext) -> Judgement | None:
    if value.strip().lower().rstrip(".") in PLACEHOLDER_ADDRESSES:
        return failed(
            "address.placeholder", SEVERITY_WARNING,
            "looks like placeholder text rather than a real address",
            value=value,
        )
    return passed("address.placeholder", SEVERITY_WARNING)


def rule_spacing(value: str, ctx: AttributeContext) -> Judgement | None:
    """Flags a lost delimiter without ever 'correcting' the value.

    Normalization deliberately passes `1720WisconsinAveNW` through unchanged —
    inventing the spaces would fabricate a value the source never wrote. This
    records that the value looks damaged so a human can go back to the source.
    """
    for token in value.split():
        if len(token) >= RUN_TOGETHER_MIN and _HAS_LOWER_UPPER_RUN.search(token):
            return failed(
                "address.spacing", SEVERITY_WARNING,
                "a long run-together token suggests whitespace was lost before "
                "this reached us; the value is kept exactly as given",
                token=token,
            )
    return passed("address.spacing", SEVERITY_WARNING)


def rule_drifted_field(value: str, ctx: AttributeContext) -> Judgement | None:
    """An email or URL in an address column means the mapping is wrong."""
    lowered = value.lower()
    if "@" in value and " " not in value.strip():
        return failed("address.drifted_field", SEVERITY_WARNING,
                      "looks like an email address in an address column", value=value)
    if lowered.startswith(("http://", "https://", "www.")):
        return failed("address.drifted_field", SEVERITY_WARNING,
                      "looks like a URL in an address column", value=value)
    return passed("address.drifted_field", SEVERITY_WARNING)


def rule_street_number(value: str, ctx: AttributeContext) -> Judgement | None:
    """Recorded, not judged.

    Most street addresses carry a number, but PO boxes, rural addresses, named
    buildings and much of the world outside street-numbering conventions do not.
    Info severity keeps it queryable without ever affecting a record's status.
    """
    if DIGITS.search(value):
        return passed("address.street_number", SEVERITY_INFO)
    return failed(
        "address.street_number", SEVERITY_INFO,
        "no digits, so this may be a building or PO box rather than a street address",
        value=value,
    )


RULES = (
    rule_length,
    rule_placeholder,
    rule_spacing,
    rule_drifted_field,
    rule_street_number,
)
