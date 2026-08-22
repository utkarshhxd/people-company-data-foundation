"""Everything the Operations tab needs, in one call.

Three questions an operator opens a console to answer, none of which any
existing endpoint covered:

  * **Is anything stopped?** A paused stage is the loudest thing this system
    can be doing and the least visible -- no container exits, no request fails,
    it simply stops loading, which looks exactly like a quiet week.
  * **Is everything still running?** A consumer wedged on a broker that accepts
    connections and never delivers keeps its container up. Liveness here is the
    age of a heartbeat, not the existence of a process.
  * **Does the data still agree with itself?** `tools/sql/verify.sql` has held
    twelve reconciliation checks since long before this console existed, and
    running them meant `docker compose cp` and a psql invocation.

Assembled server-side rather than as four fetches from the page, because these
are read together and judged together: lag means something different when a
stage is paused, and "the watcher has not beaten in an hour" means something
different when Postgres is down.
"""

import logging
import re
from pathlib import Path
from typing import Any

from common import control
from common.db import connect
from psycopg.rows import dict_row

from review_console import pipeline_metrics

logger = logging.getLogger(__name__)

# Mounted read-only, the same ./tools tree the CLI services already get. Read
# at request time rather than baked in, so editing a check does not need an
# image rebuild.
VERIFY_SQL = Path("/tools/sql/verify.sql")

# Each check in that file is announced by a psql meta-command the driver cannot
# execute, then one statement. Splitting on the announcement is what turns a
# file meant for `psql -f` into something addressable one check at a time.
_CHECK = re.compile(r"^\\echo\s+'===\s*(?P<name>.+?)\s*==='\s*$", re.MULTILINE)

# A check that hangs is worse than a check that fails: this runs from a request
# handler, and the whole point is to be able to ask during an incident.
CHECK_TIMEOUT_MS = 15_000


def _heartbeats(conn) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT service, beat_at, detail,
                   -- float8, not the numeric extract() returns: numeric
                   -- serialises to a JSON *string*, and the page compares this
                   -- against thresholds and formats it as a duration.
                   extract(epoch FROM now() - beat_at)::float8 AS age_seconds
            FROM service_heartbeat ORDER BY service
            """
        )
        return cur.fetchall()


def _kafka() -> dict[str, Any]:
    """Consumer lag, or an honest statement that it could not be read.

    Not an empty list on failure: "nothing is behind" and "we cannot tell
    whether anything is behind" are opposite states, and a page that renders
    them the same way is worse than one showing an error.
    """
    try:
        rows = pipeline_metrics._consumer_lag()
    except Exception as exc:
        logger.warning("could not read consumer lag: %s", exc)
        return {"reachable": False, "error": str(exc), "groups": []}

    groups: dict[str, dict[str, Any]] = {}
    for group, topic, partition, lag in rows:
        entry = groups.setdefault(group, {"group": group, "topic": topic, "lag": 0.0,
                                          "partitions": []})
        entry["lag"] += lag
        entry["partitions"].append({"partition": partition, "lag": lag})
    for entry in groups.values():
        entry["partitions"].sort(key=lambda p: p["partition"])
    return {
        "reachable": True,
        "groups": sorted(groups.values(), key=lambda g: g["group"]),
        "total_lag": sum(g["lag"] for g in groups.values()),
    }


def snapshot() -> dict[str, Any]:
    """Control state, liveness and lag, together."""
    with connect() as conn:
        stages = [state.as_dict() for state in control.all_states(conn)]
        heartbeats = _heartbeats(conn)

    paused = [s for s in stages if s["paused"]]
    kafka = _kafka()
    return {
        "stages": stages,
        "paused": paused,
        "services": heartbeats,
        "kafka": kafka,
        # One line the page can put at the top without re-deriving it, so the
        # two consoles cannot disagree about what counts as healthy.
        "healthy": not paused and kafka.get("reachable", False),
    }


def _checks() -> list[tuple[str, str]]:
    if not VERIFY_SQL.is_file():
        raise FileNotFoundError(
            f"{VERIFY_SQL} is not mounted; the integrity checks live in "
            "tools/sql/verify.sql and need ./tools mounted read-only"
        )
    text = VERIFY_SQL.read_text(encoding="utf-8")
    matches = list(_CHECK.finditer(text))
    out = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        if body:
            out.append((match.group("name"), body))
    return out


def integrity() -> dict[str, Any]:
    """Run the reconciliation checks. Empty output is the pass condition.

    Every check in `verify.sql` is written to return rows only when something
    is wrong, so this reports a row count and the first few offending rows --
    nobody has to interpret a number, and a failure arrives with its evidence.
    """
    results = []
    with connect(statement_timeout_ms=CHECK_TIMEOUT_MS) as conn:
        for name, sql in _checks():
            try:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(sql)
                    rows = cur.fetchall()
                results.append({
                    "name": name,
                    "ok": not rows,
                    "offending": len(rows),
                    "sample": rows[:5],
                })
            except Exception as exc:
                # A check that cannot run is not a check that passed.
                conn.rollback()
                logger.warning("integrity check %r failed to run: %s", name, exc)
                results.append({
                    "name": name, "ok": None, "offending": 0,
                    "sample": [], "error": str(exc),
                })

    failed = [r for r in results if r["ok"] is False]
    errored = [r for r in results if r["ok"] is None]
    return {
        "checks": results,
        "total": len(results),
        "failed": len(failed),
        "errored": len(errored),
        "ok": not failed and not errored,
    }
