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

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

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
