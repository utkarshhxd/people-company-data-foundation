"""What the supervisor is allowed to decide, and what it must never touch.

Two halves, both tested without a database because both were deliberately
separated from one: `assess` turns heartbeat ages into a verdict, and `decide`
turns a verdict plus the stage's current state into an action.

The rules that matter are about restraint, not about detection. An automatic
actor that stops ingestion when resolution dies is useful; one that quietly
undoes a person's decision, or the validation breaker's, is a system nobody
can trust to leave alone.
"""

from datetime import UTC, datetime, timedelta

import pytest
from common import control
from common.config import settings
from review_console import supervisor

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(hours=1)

ALL_ALIVE = dict.fromkeys(supervisor.EXPECTED, 5.0)


@pytest.fixture(autouse=True)
def _known_settings(monkeypatch):
    monkeypatch.setattr(settings, "supervisor_stale_after_seconds", 180.0)
    monkeypatch.setattr(settings, "supervisor_auto_resume", True)
    monkeypatch.setattr(settings, "supervisor_healthy_checks_before_resume", 3)


def _state(paused: bool, changed_by: str = "ada", reason: str | None = None):
    return control.ControlState(
        stage="ingestion",
        state=control.PAUSED if paused else control.RUNNING,
        reason=reason,
        changed_by=changed_by,
        evidence={},
        changed_at=NOW,
    )


def _healthy():
    return supervisor.assess(ALL_ALIVE, NOW, LONG_AGO)


def _unhealthy(**ages):
    return supervisor.assess(dict(ALL_ALIVE, **ages), NOW, LONG_AGO)


# --------------------------------------------------------------------------
# assess: liveness is the age of a heartbeat
# --------------------------------------------------------------------------


def test_everything_beating_recently_is_healthy():
    assert _healthy().ok


def test_a_service_past_the_limit_is_stale_and_named():
    health = _unhealthy(**{"resolution-consumer": 900.0})
    assert not health.ok
    assert health.stale[0]["service"] == "resolution-consumer"
    assert "resolution-consumer" in health.reason()


def test_a_service_that_has_never_beaten_is_missing_once_the_grace_has_passed():
    ages = {name: 5.0 for name in supervisor.EXPECTED if name != "watcher"}
    health = supervisor.assess(ages, NOW, LONG_AGO)
    assert health.missing == ["watcher"]
    assert "never started" in health.reason()


def test_nothing_is_called_missing_while_the_stack_is_still_coming_up():
    """A cold start has no heartbeats at all, and that is not an incident.

    Without this the supervisor would stop ingestion the moment it started,
    before a single consumer had had a chance to say anything.
    """
    just_started = NOW - timedelta(seconds=30)
    health = supervisor.assess({}, NOW, just_started)
    assert health.missing == []
    assert health.ok


def test_an_unreachable_broker_alone_is_enough_to_be_unhealthy():
    health = _healthy()
    health.broker_ok, health.broker_error = False, "no brokers available"
    assert not health.ok
    assert "no brokers available" in health.reason()


def test_the_evidence_carries_what_was_seen():
    health = _unhealthy(**{"golden-consumer": 600.0})
    evidence = health.evidence()
    assert evidence["stale"][0]["service"] == "golden-consumer"
    assert evidence["stale_after_seconds"] == 180.0


# --------------------------------------------------------------------------
# decide: when an automatic actor may act
# --------------------------------------------------------------------------


def test_a_dead_service_stops_ingestion():
    boss = supervisor.Supervisor()
    assert boss.decide(_state(paused=False), _unhealthy(watcher=900.0)) == "pause"


def test_it_does_not_stop_what_is_already_stopped_for_the_same_reason():
    boss = supervisor.Supervisor()
    health = _unhealthy(watcher=900.0)
    already = _state(paused=True, changed_by=supervisor.ACTOR,
                     reason=boss.reason_for(health))
    assert boss.decide(already, health) is None


def test_it_never_overwrites_a_persons_pause():
    """Their reason is the one that matters; overwriting it erases why."""
    boss = supervisor.Supervisor()
    theirs = _state(paused=True, changed_by="ada", reason="investigating a vendor")
    assert boss.decide(theirs, _unhealthy(watcher=900.0)) is None


def test_it_never_starts_a_stage_a_person_stopped():
    boss = supervisor.Supervisor()
    theirs = _state(paused=True, changed_by="ada", reason="investigating a vendor")
    for _ in range(10):
        assert boss.decide(theirs, _healthy()) is None


def test_it_never_starts_a_stage_the_breaker_stopped():
    """The breaker's pause is a judgement about the data, not about liveness.

    'auto' is what `validation.breaker` writes. Everything being alive again
    says nothing about whether the data is still coming back invalid.
    """
    boss = supervisor.Supervisor()
    breaker = _state(paused=True, changed_by="auto", reason="98% invalid")
    for _ in range(10):
        assert boss.decide(breaker, _healthy()) is None


def test_it_starts_its_own_pause_again_only_after_several_clean_checks():
    boss = supervisor.Supervisor()
    mine = _state(paused=True, changed_by=supervisor.ACTOR, reason="stale")
    assert boss.decide(mine, _healthy()) is None   # 1
    assert boss.decide(mine, _healthy()) is None   # 2
    assert boss.decide(mine, _healthy()) == "resume"  # 3


def test_flapping_never_accumulates_enough_to_resume():
    """A service up on every other check must not be treated as recovered."""
    boss = supervisor.Supervisor()
    mine = _state(paused=True, changed_by=supervisor.ACTOR, reason="stale")
    for _ in range(10):
        assert boss.decide(mine, _healthy()) is None
        assert boss.decide(mine, _unhealthy(watcher=900.0)) in (None, "pause")


def test_auto_resume_can_be_switched_off_entirely(monkeypatch):
    monkeypatch.setattr(settings, "supervisor_auto_resume", False)
    boss = supervisor.Supervisor()
    mine = _state(paused=True, changed_by=supervisor.ACTOR, reason="stale")
    for _ in range(10):
        assert boss.decide(mine, _healthy()) is None


def test_a_healthy_running_pipeline_is_left_alone():
    boss = supervisor.Supervisor()
    for _ in range(10):
        assert boss.decide(_state(paused=False), _healthy()) is None
