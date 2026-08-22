"""Stop a stage, and start it again, in a way that survives a restart.

Three callers put a stage into `paused`, and they mean different things:

  * **a person**, because something looks wrong and they want the pipeline to
    stop moving while they find out;
  * **the stage itself**, because what it is seeing does not look like data it
    should keep processing -- see `validation.breaker`;
  * **the supervisor**, because a service that would process the work has
    stopped reporting -- see `review_console.supervisor`.

All three write the same row. The first two are undone only by somebody
deciding it is fine and resuming. Neither clears itself: an automatic pause
that timed out would be a pause only ever observed by whoever happened to be
watching, which is the opposite of the point. Both are judgements -- about what
somebody saw, or about what the data looks like -- and a judgement needs
somebody to decide the reason is gone.

The third is not a judgement. It is a reflex to an observable fact, a process
is not beating, and the same observation says when it is over -- so the
supervisor undoes its own pause once the service has been back for several
consecutive checks. It will never undo one of the other two: `changed_by` is
what tells them apart, and it is checked before anything automatic acts.

**Pausing never discards work.** A paused stage stops *starting* work; whatever
was in flight finishes and is recorded. For the consumers this is close to
free: the partitions are paused, the offsets are not committed past what was
done, and the backlog sits in its topic until the stage runs again. For the
watcher, files stay in the drop folder. Nothing is quarantined, rejected or
moved aside because of a pause.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

STAGES = ("ingestion", "mapping", "normalization", "validation",
          "resolution", "golden")

RUNNING = "running"
PAUSED = "paused"


class StagePaused(Exception):
    """Raised by `guard` when a stage has been stopped.

    Carries the state so a caller can say *why* it is not running rather than
    only that it is not.
    """

    def __init__(self, state: "ControlState") -> None:
        # `changed_at` is NOT NULL on a real row, so the guard below is not
        # about data -- it is about this being an error path. An exception that
        # raises while describing why something stopped replaces a clear
        # message with a confusing one, at the exact moment somebody needs the
        # clear one.
        when = (f" at {state.changed_at:%Y-%m-%d %H:%M:%SZ}"
                if state.changed_at is not None else "")
        super().__init__(
            f"{state.stage} is paused: {state.reason or 'no reason recorded'} "
            f"(by {state.changed_by}{when})"
        )
        self.state = state


@dataclass(frozen=True)
class ControlState:
    stage: str
    state: str
    reason: str | None
    changed_by: str
    evidence: dict[str, Any]
    changed_at: Any

    @property
    def paused(self) -> bool:
        return self.state == PAUSED

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "state": self.state,
            "paused": self.paused,
            "reason": self.reason,
            "changed_by": self.changed_by,
            "evidence": self.evidence,
            "changed_at": self.changed_at,
        }


def _running(stage: str) -> ControlState:
    """A stage with no row has never been stopped, which is 'running'."""
    return ControlState(stage, RUNNING, None, "default", {}, None)


def get(conn: psycopg.Connection, stage: str) -> ControlState:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT stage, state, reason, changed_by, evidence, changed_at "
            "FROM pipeline_control WHERE stage = %s",
            (stage,),
        )
        row = cur.fetchone()
    return ControlState(**row) if row else _running(stage)


def all_states(conn: psycopg.Connection) -> list[ControlState]:
    """Every stage, including the ones that have never been touched."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT stage, state, reason, changed_by, evidence, changed_at "
            "FROM pipeline_control"
        )
        found = {row["stage"]: ControlState(**row) for row in cur.fetchall()}
    return [found.get(stage, _running(stage)) for stage in STAGES]


def guard(conn: psycopg.Connection, stage: str) -> None:
    """Raise if this stage is not allowed to start work right now."""
    state = get(conn, stage)
    if state.paused:
        raise StagePaused(state)


def _write(
    conn: psycopg.Connection, stage: str, to_state: str, reason: str | None,
    changed_by: str, evidence: dict[str, Any] | None, note: str | None,
) -> ControlState:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    payload = json.dumps(evidence or {})
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pipeline_control (stage, state, reason, changed_by, evidence)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (stage) DO UPDATE
            SET state = EXCLUDED.state, reason = EXCLUDED.reason,
                changed_by = EXCLUDED.changed_by, evidence = EXCLUDED.evidence,
                changed_at = now()
            RETURNING (SELECT state FROM pipeline_control WHERE stage = %s)
            """,
            (stage, to_state, reason, changed_by, payload, stage),
        )
        cur.execute(
            """
            INSERT INTO pipeline_control_event
                (stage, from_state, to_state, reason, changed_by, evidence, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (stage, RUNNING if to_state == PAUSED else PAUSED, to_state,
             reason, changed_by, payload, note),
        )
    conn.commit()
    return get(conn, stage)


def pause(
    conn: psycopg.Connection, stage: str, reason: str, changed_by: str,
    evidence: dict[str, Any] | None = None, note: str | None = None,
) -> ControlState:
    """Stop a stage. Idempotent: pausing a paused stage updates the reason."""
    logger.warning("pausing %s: %s (by %s)", stage, reason, changed_by)
    return _write(conn, stage, PAUSED, reason, changed_by, evidence, note)


def resume(
    conn: psycopg.Connection, stage: str, changed_by: str, note: str | None = None
) -> ControlState:
    """Start a stage again. The reason it stopped stays in the event history."""
    logger.warning("resuming %s (by %s)", stage, changed_by)
    return _write(conn, stage, RUNNING, None, changed_by, {}, note)


def history(
    conn: psycopg.Connection, stage: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    clause = "WHERE stage = %s" if stage else ""
    params: list[Any] = [stage] if stage else []
    params.append(limit)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT stage, from_state, to_state, reason, changed_by,
                   evidence, note, created_at
            FROM pipeline_control_event
            {clause}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            params,
        )
        return cur.fetchall()
