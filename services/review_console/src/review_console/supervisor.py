"""Stop taking work in when the things that would process it are gone.

The failure this exists for is quiet, which is what makes it worth automating.
Resolution's container dies at two in the morning. Nothing breaks: the watcher
keeps loading files, ingestion keeps committing batches, Kafka keeps accepting
announcements, and every one of them piles up on a topic nobody is reading.
By nine there is a day of backlog, a day of files in `_done/` that are not
actually done, and no single moment anybody can point at as when it started.

So the console watches, and when a service it depends on has stopped saying it
is alive, it stops ingestion -- the one stage that decides whether *new* work
enters the system. Everything already inside stays exactly where it is:
in-flight records finish and commit, queued files are held rather than failed,
and backlogs wait in their topics. What stops is the pile growing.

**Liveness is the age of a heartbeat, not the existence of a container.** A
consumer wedged on a broker that accepts connections and never delivers keeps
its container "up" and does nothing, which is precisely the case
`restart: unless-stopped` cannot see.

**This pause clears itself; the other two do not.** `common.control` is
explicit that nothing resumes on its own, and for the two callers it was
written for that is right: a person's pause is a judgement that something looks
wrong, and the validation breaker's pause is a judgement that the data coming
back is not data. Both need somebody to decide the reason is gone. This one is
not a judgement. It is a reflex to an observable fact -- a process is not
beating -- and the same observation says when it is over. So a supervisor pause
is undone by the supervisor, only when the service has been back for several
consecutive checks, and only ever a pause the supervisor itself set: it will
not touch a stage a person stopped, or one the breaker stopped. Set
`SUPERVISOR_AUTO_RESUME=false` to require a human either way.

Every transition, in both directions, is written to `pipeline_control_event`
with the evidence that caused it. An automatic action nobody can reconstruct
afterwards is worse than no automatic action.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from common import control
from common.config import settings
from common.db import connect
from psycopg.rows import dict_row

from review_console import pipeline_metrics

logger = logging.getLogger(__name__)

# Written into `changed_by`, and the thing that makes an automatic pause
# distinguishable from a person's. Nothing else may use this name.
ACTOR = "supervisor"

# The stage new work enters through. Pausing it holds the ingest queue, stops
# the watcher picking up files, and refuses a CLI load -- one switch, and the
# only one that has to be flipped, because everything downstream of it queues.
GATE = "ingestion"

# The long-running processes whose absence means work would pile up. Named
# rather than discovered, so a service that has never started once is still
# noticed as missing instead of being invisible for having no row.
EXPECTED = (
    "mapping-consumer",
    "normalization-consumer",
    "validation-consumer",
    "resolution-consumer",
    "golden-consumer",
    "watcher",
)


@dataclass
class Health:
    """What one pass saw."""

    at: datetime
    stale: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    broker_ok: bool = True
    broker_error: str | None = None

    @property
    def ok(self) -> bool:
        return not self.stale and not self.missing and self.broker_ok

    def reason(self) -> str:
        """One sentence somebody woken up at 3am can act on."""
        parts = []
        if self.missing:
            parts.append(f"never started: {', '.join(sorted(self.missing))}")
        if self.stale:
            parts.append("stopped responding: " + ", ".join(
                f"{s['service']} ({s['age_seconds']:.0f}s ago)"
                for s in sorted(self.stale, key=lambda s: s["service"])
            ))
        if not self.broker_ok:
            parts.append(f"Kafka unreachable: {self.broker_error}")
        return "; ".join(parts) or "everything responding"

    def evidence(self) -> dict[str, Any]:
        return {
            "checked_at": self.at.isoformat(),
            "stale": self.stale,
            "missing": self.missing,
            "broker_ok": self.broker_ok,
            "broker_error": self.broker_error,
            "stale_after_seconds": settings.supervisor_stale_after_seconds,
        }


def _heartbeat_ages(conn) -> dict[str, float]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT service, extract(epoch FROM now() - beat_at)::float8 AS age "
            "FROM service_heartbeat"
        )
        return {row["service"]: row["age"] for row in cur.fetchall()}


def assess(
    ages: dict[str, float],
    now: datetime,
    started_at: datetime,
    expected: tuple[str, ...] = EXPECTED,
) -> Health:
    """Turn heartbeat ages into a verdict. No IO, so it can be reasoned about.

    `started_at` is the supervisor's own start, and it exists for one case: a
    stack coming up cold, where nothing has beaten yet and every service would
    read as missing. Nothing is called missing until it has had the same grace
    period a running service gets before being called stale.
    """
    health = Health(at=now)
    limit = settings.supervisor_stale_after_seconds
    for service in expected:
        age = ages.get(service)
        if age is None:
            if (now - started_at).total_seconds() > limit:
                health.missing.append(service)
        elif age > limit:
            health.stale.append({"service": service, "age_seconds": age})
    return health


def check(started_at: datetime, expected: tuple[str, ...] = EXPECTED) -> Health:
    """One look at whether the things that process work are still there."""
    with connect() as conn:
        ages = _heartbeat_ages(conn)
    health = assess(ages, datetime.now(UTC), started_at, expected)
    health.broker_ok, health.broker_error = pipeline_metrics.broker_reachable()
    return health


class Supervisor:
    """The loop, and the small amount of memory it needs to avoid flapping."""

    def __init__(self, interval: float | None = None) -> None:
        self._interval = interval or settings.supervisor_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started_at = datetime.now(UTC)
        self.last: Health | None = None
        self.last_error: str | None = None
        # How many consecutive passes have been clean. Reset by any bad one, so
        # a service flapping every other check never accumulates enough to
        # start ingestion again and immediately stop it.
        self._consecutive_ok = 0

    # -- deciding -----------------------------------------------------------

    def decide(self, state: control.ControlState, health: Health) -> str | None:
        """What to do about this, given what the stage's state already is.

        Pure, and separated from the writing for that reason: every branch here
        is a rule about when an automatic actor may and may not overrule a
        person, which is exactly the kind of thing that should be readable and
        testable without a database.
        """
        if not health.ok:
            self._consecutive_ok = 0
            if state.paused and state.changed_by != ACTOR:
                # Somebody else's pause. Their reason is the one that matters
                # and overwriting it would erase why they stopped it; this
                # condition is visible on the Operations tab regardless.
                return None
            if state.paused and state.reason == self.reason_for(health):
                return None  # already stopped, for exactly this
            return "pause"

        self._consecutive_ok += 1
        if not state.paused or state.changed_by != ACTOR:
            # Never undoes a person's pause, and never undoes the validation
            # breaker's. Both are judgements; this is a reflex.
            return None
        if not settings.supervisor_auto_resume:
            return None
        if self._consecutive_ok < settings.supervisor_healthy_checks_before_resume:
            return None
        return "resume"

    @staticmethod
    def reason_for(health: Health) -> str:
        return f"a service this depends on is not running -- {health.reason()}"

    def act(self, health: Health) -> str | None:
        """Carry out whatever `decide` decided. Returns what it did, if anything."""
        with connect() as conn:
            state = control.get(conn, GATE)
            what = self.decide(state, health)
            if what == "pause":
                control.pause(
                    conn, GATE, self.reason_for(health), changed_by=ACTOR,
                    evidence=health.evidence(),
                    note="Stopped automatically. New work is refused; nothing "
                         "already in the system is discarded.",
                )
                return "paused"
            if what == "resume":
                control.resume(
                    conn, GATE, changed_by=ACTOR,
                    note=f"Everything responding again for "
                         f"{self._consecutive_ok} consecutive checks.",
                )
                return "resumed"
            return None

    # -- looping ------------------------------------------------------------

    def once(self) -> Health:
        health = check(self.started_at)
        self.last = health
        did = self.act(health)
        if did == "paused":
            logger.warning("stopped %s automatically: %s", GATE, health.reason())
        elif did == "resumed":
            logger.warning("started %s again: everything is responding", GATE)
        return health

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.once()
                self.last_error = None
            except Exception as exc:
                # Almost always Postgres. Nothing can be decided without it --
                # the control state lives there -- so the honest response is to
                # say so and look again.
                self.last_error = str(exc)
                logger.warning("supervisor pass failed: %s", exc)
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._thread is not None or not settings.supervisor_enabled:
            if not settings.supervisor_enabled:
                logger.warning(
                    "supervisor is switched off; nothing will stop ingestion "
                    "automatically if a service goes away"
                )
            return
        self.started_at = datetime.now(UTC)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="supervisor", daemon=True
        )
        self._thread.start()
        logger.info(
            "supervisor watching %s every %.0fs; stale after %.0fs",
            ", ".join(EXPECTED), self._interval,
            settings.supervisor_stale_after_seconds,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def as_dict(self) -> dict[str, Any]:
        """What the console shows: is it on, what did it last see, what did it do."""
        health = self.last
        return {
            "enabled": settings.supervisor_enabled,
            "auto_resume": settings.supervisor_auto_resume,
            "running": self._thread is not None and self._thread.is_alive(),
            "interval_seconds": self._interval,
            "stale_after_seconds": settings.supervisor_stale_after_seconds,
            "watching": list(EXPECTED),
            "consecutive_ok": self._consecutive_ok,
            "checks_before_resume": settings.supervisor_healthy_checks_before_resume,
            "last_error": self.last_error,
            "last": None if health is None else {
                "at": health.at,
                "ok": health.ok,
                "reason": health.reason(),
                "stale": health.stale,
                "missing": health.missing,
                "broker_ok": health.broker_ok,
                "broker_error": health.broker_error,
            },
        }


supervisor = Supervisor()
