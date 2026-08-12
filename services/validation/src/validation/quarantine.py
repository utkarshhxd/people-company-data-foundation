"""Quarantine lifecycle: what happens to a record validation called 'invalid'.

The rule the spec insists on is that invalid records must not disappear.
Quarantine enforces it from the other side too: they must not silently get
through either. A quarantined record stays exactly where it is, fully intact,
and is simply absent from `resolvable_record` until someone decides otherwise.

This module holds the transitions, deliberately separated from the database so
they can be reasoned about — and tested — on their own.
"""

from dataclasses import dataclass

STATUS_OPEN = "open"
STATUS_RELEASED = "released"
STATUS_REJECTED = "rejected"
STATUS_RESOLVED = "resolved"

ACTION_QUARANTINED = "quarantined"
ACTION_RELEASED = "released"
ACTION_REJECTED = "rejected"
ACTION_RESOLVED = "resolved"
ACTION_REOPENED = "reopened"
ACTION_REASONS_CHANGED = "reasons_changed"

SYSTEM_ACTOR = "system"


@dataclass(frozen=True)
class Transition:
    """A decided change. `None` from `route` means nothing needs to happen."""
    action: str
    from_status: str | None
    to_status: str
    reason_codes: list[str]
    actor: str = SYSTEM_ACTOR
    note: str | None = None


def route(
    validation_status: str,
    error_rules: list[str],
    current: dict | None,
) -> Transition | None:
    """Decide the quarantine consequence of a validation verdict.

    `current` is the existing quarantine_item for this record, or None.
    Called inside validation's transaction, so it must be a pure decision.
    """
    reasons = sorted(set(error_rules))
    is_invalid = validation_status == "invalid"

    if current is None:
        if not is_invalid:
            return None
        return Transition(ACTION_QUARANTINED, None, STATUS_OPEN, reasons,
                          note="failed validation with error-severity rules")

    status = current["status"]
    previous = sorted(current.get("reason_codes") or [])

    # A human's rejection is terminal. A ruleset change that happens to make the
    # record pass must not quietly undo a person's judgement — reopening it is
    # itself a human decision.
    if status == STATUS_REJECTED:
        return None

    if not is_invalid:
        if status in (STATUS_OPEN, STATUS_RELEASED):
            # The record now passes on its own, so no override is needed and no
            # human needs to look at it. This is what makes fixing a mapping or
            # a rule cheap: the backlog clears itself.
            return Transition(
                ACTION_RESOLVED, status, STATUS_RESOLVED, [],
                note="re-validation passed; no longer quarantined",
            )
        return None

    # Still invalid from here on.
    if status == STATUS_RELEASED:
        if reasons == previous:
            # Same problem the reviewer already accepted. Their decision stands.
            return None
        return Transition(
            ACTION_REOPENED, status, STATUS_OPEN, reasons,
            note=f"new failures since release: {sorted(set(reasons) - set(previous))}",
        )

    if status == STATUS_RESOLVED:
        return Transition(ACTION_QUARANTINED, status, STATUS_OPEN, reasons,
                          note="failed validation again after being resolved")

    # Already open. Only worth recording if the reasons actually changed.
    if reasons != previous:
        return Transition(ACTION_REASONS_CHANGED, status, STATUS_OPEN, reasons,
                          note="failure reasons changed while awaiting review")
    return None


def review(
    action: str, current: dict, actor: str, note: str | None = None
) -> Transition:
    """A human decision. Raises rather than guessing at an impossible transition."""
    if action not in (ACTION_RELEASED, ACTION_REJECTED, ACTION_REOPENED):
        raise ValueError(f"unknown review action {action!r}")

    status = current["status"]
    reasons = sorted(current.get("reason_codes") or [])

    if action == ACTION_REOPENED:
        if status == STATUS_OPEN:
            raise ValueError("record is already open for review")
        return Transition(ACTION_REOPENED, status, STATUS_OPEN, reasons, actor, note)

    if status == STATUS_RESOLVED:
        raise ValueError(
            "record was resolved by re-validation and is no longer quarantined; "
            "reopen it first if you disagree"
        )

    to_status = STATUS_RELEASED if action == ACTION_RELEASED else STATUS_REJECTED
    if status == to_status:
        raise ValueError(f"record is already {to_status}")
    return Transition(action, status, to_status, reasons, actor, note)
