"""Stop validating when what is coming through stops looking like data.

Nothing is lost when validation rejects everything: each record keeps its raw
payload, every judgement, and a quarantine item with its reasons. That is the
design working. The problem is what it costs to be right that way ten million
times -- a vendor changes an export format, or a rule tightens, or a mapping
goes wrong, and the pipeline spends the night faithfully quarantining an entire
feed. The moment worth stopping at is record five hundred.

So this watches the shape of the outcome rather than any individual verdict:

  **A rate, not a count.** Fifty invalid records out of fifty thousand is a
  vendor with messy data, which is the entire premise of this system. Fifty out
  of fifty is something broken upstream.

  **Only above a floor.** A two-row file where both rows are invalid is not
  evidence of anything, and tripping on it would teach everybody to ignore the
  breaker -- which is the only way a circuit breaker really fails.

  **Never on a batch that was already going to be fine.** The threshold is
  deliberately high. This is not a data-quality alarm; `RecordsBeingQuarantined`
  is the alarm. This is the thing that stops the machine.

What it does *not* do is decide anything about the records themselves. A record
validated before the breaker tripped keeps its verdict; a record that never got
validated is untouched. Tripping stops the *next* unit of work, and a human
starts it again once they know why.
"""

import logging
from dataclasses import dataclass

from common import control
from common.config import settings

logger = logging.getLogger(__name__)

STAGE = "validation"


@dataclass(frozen=True)
class Verdict:
    """Whether the run so far looks like a broken pipeline."""

    tripped: bool
    records: int
    invalid: int
    rate: float
    dominant_rules: list[tuple[str, int]]

    def reason(self) -> str:
        pct = f"{self.rate:.0%}"
        if self.dominant_rules:
            rule, count = self.dominant_rules[0]
            return (
                f"{self.invalid} of {self.records} records invalid ({pct}); "
                f"most common failure is {rule} on {count} of them"
            )
        return f"{self.invalid} of {self.records} records invalid ({pct})"

    def evidence(self) -> dict:
        return {
            "records": self.records,
            "invalid": self.invalid,
            "invalid_rate": round(self.rate, 4),
            "threshold": settings.validation_breaker_threshold,
            "minimum_records": settings.validation_breaker_min_records,
            "failed_rules": [
                {"rule_id": rule, "records": count}
                for rule, count in self.dominant_rules
            ],
        }


def assess(
    records: int, invalid: int, failed_rules: dict[str, int] | None = None
) -> Verdict:
    """Judge a run's outcome. Pure -- takes counts, touches nothing."""
    rate = (invalid / records) if records else 0.0
    dominant = sorted(
        (failed_rules or {}).items(), key=lambda item: (-item[1], item[0])
    )[:5]
    tripped = (
        settings.validation_breaker_enabled
        and records >= settings.validation_breaker_min_records
        and rate >= settings.validation_breaker_threshold
    )
    return Verdict(tripped, records, invalid, rate, dominant)


def trip_if_broken(
    conn, records: int, invalid: int, failed_rules: dict[str, int] | None = None,
    context: str = "",
) -> Verdict:
    """Assess, and stop validation if the answer is bad enough.

    Returns the verdict either way, so a caller can report a near-miss without
    having to recompute it.
    """
    verdict = assess(records, invalid, failed_rules)
    if not verdict.tripped:
        return verdict

    evidence = verdict.evidence()
    if context:
        evidence["context"] = context
    control.pause(
        conn, STAGE, verdict.reason(), changed_by="auto", evidence=evidence,
        note=(
            "Tripped automatically. Nothing has been lost: every record already "
            "validated kept its verdict and its quarantine reasons, and nothing "
            "further will be validated until this is resumed."
        ),
    )
    logger.error(
        "validation stopped itself: %s. Resume with: docker compose run --rm "
        "validation validation-control resume --reviewed-by <you>",
        verdict.reason(),
    )
    return verdict
