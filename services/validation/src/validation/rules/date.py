"""Date rules: when something was reported to have happened.

Only two things are checkable without knowing the field's meaning: a date cannot
have happened after today, and a date centuries before the thing it describes
existed is a parsing artefact rather than a fact.

Format is not checked here at all. The normalizer accepts only unambiguous
layouts, so anything that reached a rule is already well formed; a value it
rejected arrives as None and is reported by the normalization-outcome rule
instead.
"""

from datetime import UTC, date, datetime, timedelta

from validation.rules.base import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    AttributeContext,
    Judgement,
    failed,
    passed,
)

# A day of slack. Vendors export in local time and we compare in UTC, so a
# genuinely current date can read as tomorrow without anything being wrong.
FUTURE_TOLERANCE = timedelta(days=1)
MIN_DATE = date(1900, 1, 1)


def _parse(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def rule_input(value: str, ctx: AttributeContext) -> Judgement | None:
    """Whether the source wrote a date or a number we read as one.

    An xlsx export turns a date column into Excel's day count, so '45047' means
    2023-05-01. Decoding it is right — the alternative is discarding a date the
    vendor did state — but it is our arithmetic on an integer, and a column of
    integers that is not really dates would decode just as willingly. Saying so
    keeps the difference visible instead of indistinguishable from an ISO date.
    """
    if not ctx.raw_value.strip().isdigit():
        return passed("date.input", SEVERITY_WARNING)
    return failed(
        "date.input", SEVERITY_WARNING,
        "date was decoded from an Excel serial number rather than stated",
        raw_value=ctx.raw_value, normalized_value=value,
    )


def rule_not_in_the_future(value: str, ctx: AttributeContext) -> Judgement | None:
    parsed = _parse(value)
    if parsed is None:
        return None
    today = datetime.now(UTC).date()
    if parsed > today + FUTURE_TOLERANCE:
        return failed("date.future", SEVERITY_ERROR,
                      f"{value} is in the future", value=value, today=today.isoformat())
    return passed("date.future", SEVERITY_ERROR, value=value)


def rule_not_implausibly_old(value: str, ctx: AttributeContext) -> Judgement | None:
    parsed = _parse(value)
    if parsed is None:
        return None
    if parsed < MIN_DATE:
        return failed("date.range", SEVERITY_WARNING,
                      f"{value} predates {MIN_DATE.isoformat()}, which usually means "
                      "an epoch default rather than a real date",
                      value=value)
    return passed("date.range", SEVERITY_WARNING, value=value)


RULES = (rule_input, rule_not_in_the_future, rule_not_implausibly_old)
