"""Numeric rules: employee counts, founding years, and the integers behind them."""

import re
from datetime import UTC, datetime

from validation.rules.base import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    AttributeContext,
    Judgement,
    failed,
    passed,
)

MAX_EMPLOYEES = 5_000_000  # comfortably above the largest employer on earth
MIN_FOUNDED_YEAR = 1600

NUMERIC_INPUT = re.compile(r"^\d+$")


def rule_input(value: str, ctx: AttributeContext) -> Judgement | None:
    """The RAW value is judged here, not the normalized one.

    '50-100' and '1,200+' normalize to digit strings that look clean but mean
    something the source never said. The distortion is only visible upstream.
    """
    raw = ctx.raw_value.strip()
    if NUMERIC_INPUT.match(raw):
        return passed("integer.input", SEVERITY_WARNING)
    return failed(
        "integer.input", SEVERITY_WARNING,
        "raw value is not a plain integer, so the normalized number may distort it",
        raw_value=ctx.raw_value, normalized_value=value,
    )


def rule_employee_count_range(value: str, ctx: AttributeContext) -> Judgement | None:
    if ctx.canonical_field != "employee_count":
        return None
    count = int(value)
    if count > MAX_EMPLOYEES:
        return failed("employee_count.range", SEVERITY_ERROR,
                      f"{count} exceeds any plausible headcount", count=count)
    if count == 0:
        return failed("employee_count.range", SEVERITY_WARNING,
                      "zero employees is unusual for an operating company", count=count)
    return passed("employee_count.range", SEVERITY_ERROR, count=count)


def rule_founded_year_range(value: str, ctx: AttributeContext) -> Judgement | None:
    if ctx.canonical_field != "founded_year":
        return None
    year = int(value)
    current = datetime.now(UTC).year
    if year < MIN_FOUNDED_YEAR:
        return failed("founded_year.range", SEVERITY_ERROR,
                      f"{year} predates {MIN_FOUNDED_YEAR}", year=year)
    if year > current:
        return failed("founded_year.range", SEVERITY_ERROR,
                      f"{year} is in the future", year=year, current_year=current)
    return passed("founded_year.range", SEVERITY_ERROR, year=year)


RULES = (rule_input, rule_employee_count_range, rule_founded_year_range)
