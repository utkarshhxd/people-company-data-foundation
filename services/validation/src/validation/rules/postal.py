"""Postal-code rules.

Warnings only, throughout. Postal formats vary far too much between countries to
reject a value without knowing which country it belongs to, and this pipeline
does not reliably know that — normalization refuses to infer country from any
field. Shape checks that would be errors in a single-country system are warnings
here on purpose.
"""

import re

from validation.rules.base import (
    SEVERITY_WARNING,
    AttributeContext,
    Judgement,
    failed,
    passed,
)

MIN_LENGTH = 3
MAX_LENGTH = 10
POSTAL_CHARS = re.compile(r"^[A-Z0-9][A-Z0-9 -]*$")

# Recognised only to confirm a value is plausible, never to reject one that
# matches nothing here.
US_ZIP = re.compile(r"^\d{5}(-\d{4})?$")


def rule_shape(value: str, ctx: AttributeContext) -> Judgement | None:
    if MIN_LENGTH <= len(value) <= MAX_LENGTH and POSTAL_CHARS.match(value):
        return passed("postal_code.shape", SEVERITY_WARNING)
    return failed("postal_code.shape", SEVERITY_WARNING,
                  "unusual shape for a postal code", value=value, length=len(value))


def rule_us_zip_truncation(value: str, ctx: AttributeContext) -> Judgement | None:
    """Catches the leading zero that a spreadsheet ate: 00501 arriving as 501.

    Ingestion disables type inference precisely so this cannot happen here, but
    the damage may already have occurred in the vendor's own export, and only
    this check would notice.
    """
    if not value.isdigit() or len(value) >= 5:
        return passed("postal_code.us_zip_truncation", SEVERITY_WARNING)
    return failed(
        "postal_code.us_zip_truncation", SEVERITY_WARNING,
        f"{len(value)} digits — if this is a US ZIP, a leading zero was lost "
        "before the file reached us",
        value=value,
    )


RULES = (rule_shape, rule_us_zip_truncation)
