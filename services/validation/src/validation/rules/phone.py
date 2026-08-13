"""Phone rules."""

from validation.rules.base import (
    DIGITS,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    AttributeContext,
    Judgement,
    failed,
    passed,
)

# E.164 caps a full international number at 15 digits; 7 is about the shortest
# real subscriber number. Outside that range the value is not a phone number.
MIN_DIGITS = 7
MAX_DIGITS = 15

# A number made of one repeated digit, or a simple run, is filler rather than a
# contact. Seen in real vendor exports as 0000000000 and 1234567890.
FILLER = {"0", "1", "9"}
SEQUENCES = ("0123456789", "1234567890", "9876543210")


def rule_digit_count(value: str, ctx: AttributeContext) -> Judgement | None:
    digits = len(DIGITS.findall(value))
    if MIN_DIGITS <= digits <= MAX_DIGITS:
        return passed("phone.digit_count", SEVERITY_ERROR, digits=digits)
    return failed(
        "phone.digit_count", SEVERITY_ERROR,
        f"{digits} digits is outside the dialable range {MIN_DIGITS}-{MAX_DIGITS}",
        digits=digits,
    )


def rule_country_code(value: str, ctx: AttributeContext) -> Judgement | None:
    """A warning, never an error — normalization refuses to guess a country.

    Recording the ambiguity is the honest move: the number may well be fine,
    but nothing in the file says which country it belongs to.
    """
    if value.startswith("+"):
        return passed("phone.country_code", SEVERITY_WARNING)
    return failed(
        "phone.country_code", SEVERITY_WARNING,
        "no country code, so the number is only dialable if the country is known",
    )


def rule_filler(value: str, ctx: AttributeContext) -> Judgement | None:
    """Catches 0000000000 and 1234567890 — well-formed, and not a phone number."""
    digits = "".join(DIGITS.findall(value))
    if not digits:
        return None
    if len(set(digits)) == 1 and digits[0] in FILLER:
        return failed("phone.filler", SEVERITY_WARNING,
                      "a single repeated digit is placeholder text, not a number",
                      value=value)
    if any(digits in sequence for sequence in SEQUENCES) and len(digits) >= MIN_DIGITS:
        return failed("phone.filler", SEVERITY_WARNING,
                      "a digit sequence is placeholder text, not a number",
                      value=value)
    return passed("phone.filler", SEVERITY_WARNING)


def rule_extension_lost(value: str, ctx: AttributeContext) -> Judgement | None:
    """Normalization keeps digits only, so 'x204' silently joins the number.

    The raw value is inspected because the damage is invisible afterwards: the
    normalized form looks like a longer, valid number.
    """
    lowered = ctx.raw_value.lower()
    for marker in (" x", "ext", "ext.", "extn"):
        if marker in lowered:
            return failed(
                "phone.extension_merged", SEVERITY_WARNING,
                "the raw value contains an extension, which normalization has "
                "merged into the number itself",
                raw_value=ctx.raw_value, normalized_value=value,
            )
    return passed("phone.extension_merged", SEVERITY_WARNING)


RULES = (rule_digit_count, rule_country_code, rule_filler, rule_extension_lost)
