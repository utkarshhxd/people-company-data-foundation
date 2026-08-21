"""Pausing a stage, in the parts that do not need a database.

The round-trip through Postgres is exercised live (see the runbook); what is
worth pinning here is the shape of the state and the message it produces,
because that message is what somebody reads at the point they are trying to
work out why nothing is loading.
"""

from datetime import UTC, datetime

import pytest
from common import control


def _state(**overrides):
    base = {
        "stage": "validation",
        "state": control.PAUSED,
        "reason": "480 of 500 records invalid (96%)",
        "changed_by": "auto",
        "evidence": {"records": 500, "invalid": 480},
        "changed_at": datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
    }
    return control.ControlState(**{**base, **overrides})


def test_a_stage_nobody_has_touched_is_running():
    """No row means never stopped, not unknown. A stage that had to be
    explicitly marked running before it would run is a stage that does not run
    on a fresh database."""
    state = control._running("validation")
    assert not state.paused
    assert state.state == control.RUNNING


def test_guard_says_why_it_refused_not_just_that_it_did():
    exc = control.StagePaused(_state())
    message = str(exc)
    assert "validation is paused" in message
    assert "480 of 500 records invalid" in message
    assert "auto" in message


def test_a_pause_with_no_reason_still_reads_as_a_sentence():
    """Nothing should write one, but a message that renders as 'paused: None'
    is worse than useless at 3am."""
    assert "no reason recorded" in str(control.StagePaused(_state(reason=None)))


def test_the_state_serialises_with_paused_spelled_out():
    """The console branches on this, and `state == "paused"` scattered through
    a page is how one typo becomes a dashboard that says everything is fine."""
    payload = _state().as_dict()
    assert payload["paused"] is True
    assert payload["state"] == "paused"
    assert payload["evidence"]["invalid"] == 480


def test_an_unknown_stage_is_refused_rather_than_created():
    """The table is keyed by stage name. A typo that silently inserts
    'validaton' would pause nothing and report success."""
    with pytest.raises(ValueError, match="unknown stage"):
        control._write(None, "validaton", control.PAUSED, "x", "me", None, None)
