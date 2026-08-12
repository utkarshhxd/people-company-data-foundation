import pytest
from validation.quarantine import (
    ACTION_QUARANTINED,
    ACTION_REASONS_CHANGED,
    ACTION_REJECTED,
    ACTION_RELEASED,
    ACTION_REOPENED,
    ACTION_RESOLVED,
    STATUS_OPEN,
    STATUS_REJECTED,
    STATUS_RELEASED,
    STATUS_RESOLVED,
    review,
    route,
)


def item(status, reason_codes=()):
    return {"status": status, "reason_codes": list(reason_codes)}


# --------------------------------------------------------------------------
# routing (automatic, inside validation's transaction)
# --------------------------------------------------------------------------

def test_an_invalid_record_with_no_history_is_quarantined():
    transition = route("invalid", ["email.syntax"], None)
    assert transition.action == ACTION_QUARANTINED
    assert transition.to_status == STATUS_OPEN
    assert transition.reason_codes == ["email.syntax"]


@pytest.mark.parametrize("status", ["valid", "warning"])
def test_a_passing_record_is_never_quarantined(status):
    """Warnings are not quarantine-worthy, or a real vendor file would vanish."""
    assert route(status, [], None) is None


def test_re_validation_passing_closes_an_open_item_without_a_human():
    """Fixing a mapping or a rule must clear the backlog by itself."""
    transition = route("valid", [], item(STATUS_OPEN, ["record.has_identifier"]))
    assert transition.action == ACTION_RESOLVED
    assert transition.to_status == STATUS_RESOLVED
    assert transition.reason_codes == []


def test_the_same_failure_twice_does_not_churn_the_item():
    assert route("invalid", ["email.syntax"], item(STATUS_OPEN, ["email.syntax"])) is None


def test_a_new_failure_while_awaiting_review_is_recorded():
    transition = route(
        "invalid", ["email.syntax", "phone.digit_count"], item(STATUS_OPEN, ["email.syntax"])
    )
    assert transition.action == ACTION_REASONS_CHANGED
    assert transition.to_status == STATUS_OPEN
    assert transition.reason_codes == ["email.syntax", "phone.digit_count"]


def test_a_reviewers_release_survives_the_same_failure_recurring():
    """They already accepted this exact problem; re-running must not undo them."""
    assert route("invalid", ["email.syntax"], item(STATUS_RELEASED, ["email.syntax"])) is None


def test_a_new_failure_reopens_a_released_record():
    """The reviewer signed off on one problem, not on a different one."""
    transition = route(
        "invalid", ["email.syntax", "founded_year.range"],
        item(STATUS_RELEASED, ["email.syntax"]),
    )
    assert transition.action == ACTION_REOPENED
    assert transition.to_status == STATUS_OPEN
    assert "founded_year.range" in transition.note


def test_a_rejection_is_terminal_even_if_validation_later_passes():
    """A ruleset change must not quietly overturn a person's judgement."""
    assert route("valid", [], item(STATUS_REJECTED, ["email.syntax"])) is None
    assert route("invalid", ["email.syntax"], item(STATUS_REJECTED, ["email.syntax"])) is None


def test_a_resolved_record_that_breaks_again_is_re_quarantined():
    transition = route("invalid", ["email.syntax"], item(STATUS_RESOLVED))
    assert transition.action == ACTION_QUARANTINED
    assert transition.to_status == STATUS_OPEN


def test_reason_codes_are_deduplicated_and_ordered():
    """Stable ordering is what lets 'same problem' be compared cheaply."""
    transition = route("invalid", ["b.rule", "a.rule", "b.rule"], None)
    assert transition.reason_codes == ["a.rule", "b.rule"]


# --------------------------------------------------------------------------
# review (human decisions)
# --------------------------------------------------------------------------

def test_release_records_the_reviewer():
    transition = review(ACTION_RELEASED, item(STATUS_OPEN, ["email.syntax"]),
                        "utkarsh", "vendor confirmed the address by phone")
    assert transition.to_status == STATUS_RELEASED
    assert transition.actor == "utkarsh"
    assert transition.note == "vendor confirmed the address by phone"


def test_reject_keeps_the_record_but_never_resolves_it():
    transition = review(ACTION_REJECTED, item(STATUS_OPEN, ["record.has_identifier"]), "utkarsh")
    assert transition.to_status == STATUS_REJECTED


def test_a_decided_record_can_be_reopened():
    transition = review(ACTION_REOPENED, item(STATUS_REJECTED, ["email.syntax"]), "utkarsh")
    assert transition.to_status == STATUS_OPEN
    assert transition.from_status == STATUS_REJECTED


def test_reopening_an_open_record_is_refused():
    with pytest.raises(ValueError, match="already open"):
        review(ACTION_REOPENED, item(STATUS_OPEN), "utkarsh")


def test_deciding_the_same_way_twice_is_refused():
    with pytest.raises(ValueError, match="already released"):
        review(ACTION_RELEASED, item(STATUS_RELEASED), "utkarsh")


def test_a_record_resolved_by_re_validation_cannot_be_silently_released():
    """It is no longer held back, so 'release' would misrepresent what happened."""
    with pytest.raises(ValueError, match="reopen it first"):
        review(ACTION_RELEASED, item(STATUS_RESOLVED), "utkarsh")


def test_an_unknown_review_action_is_refused():
    with pytest.raises(ValueError, match="unknown review action"):
        review("deleted", item(STATUS_OPEN), "utkarsh")
