"""Proof that a long-running process is still turning its loop.

`restart: unless-stopped` only acts on a process that *exited*. A consumer
blocked on a broker that accepts connections and never delivers, or a watcher
wedged mid-sweep, keeps its container "up" and looks identical from outside to
one that simply has nothing to do. The difference is observable only if the
process says so periodically, so each one touches a file and the healthcheck
reads how old it is.

Never fatal. A process that cannot write its heartbeat is still a process that
can do its job, and stopping over it would trade a monitoring gap for an
outage -- so a failure here is logged once and carried on from. Staleness reads
the same either way: a file that stops being updated and a file that was never
written both fail the check.
"""

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Where a healthcheck looks. Overridable per service so a container and a
# laptop can differ, and so two services on one host do not share a file.
ENV_VAR = "PCDF_HEARTBEAT"


def default_path(service: str) -> Path:
    return Path(os.environ.get(ENV_VAR, f"/tmp/pcdf-{service}-heartbeat"))


def beat(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not write heartbeat %s: %s", path, exc)


def age_seconds(path: Path) -> float | None:
    """How long since the last beat, or None if there has never been one."""
    try:
        return (datetime.now(UTC).timestamp() - path.stat().st_mtime)
    except OSError:
        return None


# The file answers the container healthcheck; the row answers the console.
# A file is only visible inside the container that wrote it, and "is the
# watcher sweeping, are the consumers consuming" is precisely what someone
# opens the console to find out. Postgres is the one thing every process here
# already talks to.
#
# Throttled because a consumer beats on every poll -- once a second -- and the
# question this answers has a resolution of tens of seconds, not one.
DB_MIN_INTERVAL_SECONDS = 15.0

_last_recorded = 0.0

_UPSERT = """
INSERT INTO service_heartbeat (service, beat_at, detail)
VALUES (%s, now(), %s)
ON CONFLICT (service) DO UPDATE
SET beat_at = now(), detail = EXCLUDED.detail
"""


def record(service: str, detail: dict[str, Any] | None = None) -> None:
    """Say this process is alive, where anything with the database can see it.

    Never fatal, and never the reason a service stops: a process that cannot
    write its heartbeat is still a process doing its job. A failure here means
    the row goes stale, which reads the same as the process having stopped --
    the safe direction for a liveness signal to fail in.
    """
    global _last_recorded
    now = time.monotonic()
    if now - _last_recorded < DB_MIN_INTERVAL_SECONDS:
        return
    _last_recorded = now
    try:
        from common.db import connect

        with connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(_UPSERT, (service, json.dumps(detail or {})))
    except Exception as exc:
        logger.warning("could not record heartbeat for %s: %s", service, exc)
