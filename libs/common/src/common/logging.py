"""One logging setup, so `LOG_LEVEL` means something.

Every service already accepts `LOG_LEVEL` -- it is in `.env.example`, it is
passed to eight services in `docker-compose.yml`, and it is a field on
`Settings`. Nothing read it. Each CLI called `logging.basicConfig(level=INFO)`
with the value hardcoded, so turning the knob changed nothing, and the review
console called `basicConfig` at all: uvicorn configures its own loggers and
leaves the root logger bare, so everything this application logged below
WARNING was discarded and everything at WARNING went out through
`logging.lastResort` -- no timestamp, no level, no logger name. The startup
line announcing that the console is serving personal data unauthenticated
arrived as a naked sentence in the log.

Timestamps are UTC and explicit. A container's local time is whatever the base
image decided, and correlating two services' logs across a timezone guess is
not a thing anyone should have to do during an incident.
"""

import logging
import os
import sys
import time

from common.config import settings

FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

# Noisy at DEBUG and rarely the thing being debugged: psycopg logs every
# statement, and multipart logs every part of every upload.
_QUIETER = ("psycopg", "psycopg.pool", "multipart", "python_multipart")


def resolve_level(level: str | None = None) -> int:
    """The configured level, falling back to INFO rather than raising.

    A typo in `LOG_LEVEL` must not stop a service from starting. Logging at the
    wrong verbosity is recoverable; refusing to boot because of a log setting
    is not a trade anyone wants at 3am.
    """
    name = (level or settings.log_level or "info").strip().upper()
    resolved = logging.getLevelNamesMapping().get(name)
    if resolved is None:
        logging.getLogger(__name__).warning(
            "LOG_LEVEL=%r is not a level name; using INFO", name
        )
        return logging.INFO
    return resolved


def configure(
    service: str, level: str | None = None, to_database: bool | None = None
) -> None:
    """Set up root logging for one process. Safe to call more than once.

    `force=True` because uvicorn may have installed handlers first, and two
    sets of handlers on the root logger means every line printed twice.

    `to_database` additionally copies WARNING and above into `service_log`, so
    the console can show what the services said without host access to eleven
    containers' stdout. Off unless asked for, and never load-bearing: see
    `common.dblog` for why it drops rather than blocks.
    """
    logging.basicConfig(
        level=resolve_level(level),
        format=FORMAT,
        datefmt=DATE_FORMAT,
        stream=sys.stderr,
        force=True,
    )
    logging.Formatter.converter = time.gmtime
    for noisy in _QUIETER:
        logging.getLogger(noisy).setLevel(max(resolve_level(level), logging.INFO))

    # The same name Postgres will report. Every service shares one database
    # user and one database, so without this `pg_stat_activity` shows nine
    # identical rows and the question "what is holding this lock" has no
    # answer. Set here because this is the one call every entry point already
    # makes, and only when the operator has not named it explicitly.
    if "APPLICATION_NAME" not in os.environ:
        settings.application_name = f"pcdf-{service}"

    wanted = settings.log_to_database if to_database is None else to_database
    if wanted:
        from common import dblog

        dblog.install(service, max_rows=settings.log_database_max_rows)

    logging.getLogger(service).debug(
        "logging configured at %s", logging.getLevelName(resolve_level(level))
    )
