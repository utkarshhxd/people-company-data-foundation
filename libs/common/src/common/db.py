"""One way to open a connection, so the timeouts are not a per-caller decision.

`settings.dsn()` already carries `connect_timeout`, an `application_name` and a
default `statement_timeout` (see `common.config`). Both overrides are threaded
through here for the one caller that must not inherit the statement timeout:
schema migrations.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from common.config import settings

# Passed as statement_timeout_ms to lift the limit for one connection.
NO_STATEMENT_TIMEOUT = 0


@contextmanager
def connect(
    autocommit: bool = False,
    statement_timeout_ms: int | None = None,
    application_name: str | None = None,
) -> Iterator[psycopg.Connection]:
    with psycopg.connect(
        settings.dsn(statement_timeout_ms, application_name), autocommit=autocommit
    ) as conn:
        yield conn
