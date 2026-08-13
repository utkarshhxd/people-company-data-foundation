"""Person-name rules."""

from validation.rules.base import (
    DIGITS,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    AttributeContext,
    Judgement,
    failed,
    passed,
)

# Filler that survives normalization because it is not a null token.
PLACEHOLDER_NAMES = {
    "test", "tests", "testing", "asdf", "qwerty", "xxx", "xxxx", "abc", "aaa",
    "sample", "dummy", "example", "tbd", "todo", "no name", "noname", "name",
    "first last", "john doe", "jane doe", "firstname lastname", "n a",
}

# Honorifics sitting alone in a name column: real text, no person named.
BARE_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "madam", "mx"}

MIN_LENGTH = 2
MAX_DIGITS_ALLOWED = 3


def rule_length(value: str, ctx: AttributeContext) -> Judgement | None:
    if len(value) >= MIN_LENGTH:
        return passed("name.length", SEVERITY_ERROR, length=len(value))
    return failed(
        "name.length", SEVERITY_ERROR,
        f"{len(value)} character(s) is too short to be a name",
        length=len(value),
    )


def rule_placeholder(value: str, ctx: AttributeContext) -> Judgement | None:
    cleaned = value.strip().lower()
    if cleaned in PLACEHOLDER_NAMES:
        return failed("name.placeholder", SEVERITY_WARNING,
                      "looks like filler text rather than a real name", value=value)
    if cleaned.rstrip(".") in BARE_TITLES:
        return failed("name.placeholder", SEVERITY_WARNING,
                      "an honorific on its own names no one", value=value)
    return passed("name.placeholder", SEVERITY_WARNING)


def rule_shape(value: str, ctx: AttributeContext) -> Judgement | None:
    """Catches columns that drifted: an email or an ID sitting in a name field."""
    if "@" in value:
        return failed("name.shape", SEVERITY_WARNING,
                      "contains '@', so this may be an email in a name column",
                      value=value)
    if len(DIGITS.findall(value)) >= MAX_DIGITS_ALLOWED:
        return failed("name.shape", SEVERITY_WARNING,
                      "contains 3+ digits, so this may be an identifier",
                      value=value)
    return passed("name.shape", SEVERITY_WARNING)


def rule_full_name_completeness(value: str, ctx: AttributeContext) -> Judgement | None:
    """A single token in full_name is usually a first name only.

    Only a warning: mononyms are real, and plenty of cultures use them.
    """
    if ctx.canonical_field != "full_name":
        return None
    if len(value.split()) >= 2:
        return passed("full_name.completeness", SEVERITY_WARNING)
    return failed(
        "full_name.completeness", SEVERITY_WARNING,
        "only one name part, so this may be a partial name",
        value=value,
    )


RULES = (rule_length, rule_placeholder, rule_shape, rule_full_name_completeness)
