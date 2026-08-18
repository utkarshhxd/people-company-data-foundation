"""Monetary rules: revenue, funding totals, and the amounts behind them.

Money is checked for magnitude rather than for correctness, because nothing here
can know what a company actually earns. What it can know is that a figure is
outside the range any company on earth occupies, which almost always means the
column held something else — a share count, a market cap in cents, an id.

Currency is deliberately absent from every rule. The normalizer does not capture
it, because vendors ship amounts without saying, and a rule that assumed dollars
would quietly convert a guess into a judgement.
"""

import re

from validation.rules.base import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    AttributeContext,
    Judgement,
    failed,
    passed,
)

# Roughly fifteen times the revenue of the largest company on earth. Set this
# far out on purpose: the rule exists to catch a column holding the wrong kind
# of number, not to adjudicate between large companies.
MAX_AMOUNT = 10_000_000_000_000

PLAIN_AMOUNT = re.compile(r"^-?\d+$")


def rule_input(value: str, ctx: AttributeContext) -> Judgement | None:
    """Judge the RAW value, the same way integers are judged.

    '$1.2M' and '3.4bn' normalize to exact-looking figures the source never
    wrote. The multiplication is almost certainly right, but it is inference,
    and inference should be visible rather than silently indistinguishable from
    a figure the vendor stated digit for digit.
    """
    raw = ctx.raw_value.strip().replace(",", "")
    if PLAIN_AMOUNT.match(raw):
        return passed("money.input", SEVERITY_WARNING)
    return failed(
        "money.input", SEVERITY_WARNING,
        "raw amount was not a plain number, so the normalized figure was inferred "
        "from a symbol or magnitude suffix",
        raw_value=ctx.raw_value, normalized_value=value,
    )


def rule_amount_range(value: str, ctx: AttributeContext) -> Judgement | None:
    amount = int(value)
    if amount < 0:
        return failed("money.range", SEVERITY_ERROR,
                      f"{amount} is negative", amount=amount)
    if amount > MAX_AMOUNT:
        return failed("money.range", SEVERITY_ERROR,
                      f"{amount} exceeds any plausible amount, so the column "
                      "probably does not hold money", amount=amount)
    if amount == 0:
        # Zero funding is a real and common answer. Zero revenue for an
        # operating company is not, but it is not impossible either.
        return failed("money.range", SEVERITY_WARNING,
                      "reported as zero", amount=amount)
    return passed("money.range", SEVERITY_ERROR, amount=amount)


RULES = (rule_input, rule_amount_range)
